#!/usr/bin/env python3
"""Local SDXL-Turbo image generation helper for the Omarchy Assistant.

Reads one JSON request on stdin and writes one JSON result on stdout. Images are
written to a private temporary file that the caller moves into place, so this
helper never chooses a destination path for the user.

Commands:
  generate   read {"prompt", "aspect_ratio", "steps", "seed"} from stdin
  doctor     report interpreter, torch/CUDA, and cached weights status
  selftest   validate request handling and size mapping without any models
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

# Sized so every entry stays near 0.5 megapixels, which keeps peak VRAM small
# enough for 6 GB cards while still matching the aspect ratio the user asked for.
ASPECT_SIZES: dict[str, tuple[int, int]] = {
    "1:1": (768, 768),
    "2:3": (576, 864),
    "3:2": (864, 576),
    "3:4": (576, 768),
    "4:3": (768, 576),
    "4:5": (640, 800),
    "5:4": (800, 640),
    "9:16": (576, 1024),
    "16:9": (1024, 576),
    "21:9": (1024, 448),
}

DEFAULT_MODEL = "stabilityai/sdxl-turbo"
DEFAULT_STEPS = 2
MAX_STEPS = 4
MAX_PROMPT = 4000
VRAM_OFFLOAD_THRESHOLD = 8 * 1024**3
HF_HOME = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface")


def emit(payload: dict) -> None:
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")
    sys.stdout.flush()


def fail(error: str, **extra) -> None:
    emit({"ok": False, "error": error, **extra})


def sizes_for(aspect_ratio: str) -> tuple[int, int]:
    return ASPECT_SIZES[aspect_ratio]


def validate(request: dict) -> tuple[str, str, int, int, str]:
    prompt = str(request.get("prompt") or "").strip()
    if len(prompt) < 3 or len(prompt) > MAX_PROMPT:
        raise ValueError(f"prompt must be between 3 and {MAX_PROMPT} characters")
    aspect_ratio = str(request.get("aspect_ratio") or "1:1").strip()
    if aspect_ratio not in ASPECT_SIZES:
        raise ValueError("aspect_ratio must be one of: " + ", ".join(ASPECT_SIZES))
    try:
        steps = int(request.get("steps") or DEFAULT_STEPS)
    except (TypeError, ValueError):
        steps = DEFAULT_STEPS
    steps = max(1, min(steps, MAX_STEPS))
    try:
        seed = int(request.get("seed"))
    except (TypeError, ValueError):
        seed = int.from_bytes(os.urandom(4), "big")
    model = str(request.get("model") or os.environ.get("ASSISTANT_IMAGE_LOCAL_MODEL") or DEFAULT_MODEL).strip()
    # Model ids look like stabilityai/sdxl-turbo; anything else is ignored so a
    # request can never point the loader at an arbitrary local path.
    if not re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", model):
        model = DEFAULT_MODEL
    return prompt, aspect_ratio, steps, seed, model


def weights_cached(model: str) -> bool:
    folder = "models--" + model.replace("/", "--")
    try:
        snapshots = HF_HOME / "hub" / folder / "snapshots"
        if not snapshots.is_dir():
            return False
        for snapshot in snapshots.iterdir():
            if (snapshot / "model_index.json").is_file():
                return True
    except OSError:
        return False
    return False


def runtime_report(model: str) -> dict:
    report: dict = {"model": model, "weights_cached": weights_cached(model), "hf_home": str(HF_HOME), "warnings": []}
    try:
        import torch  # noqa: PLC0415
    except Exception as error:  # pragma: no cover - depends on the environment
        report["torch"] = f"missing ({error})"
        report["runtime"] = "none"
        report["warnings"].append("PyTorch is not installed in the local image environment.")
        return report

    report["torch"] = torch.__version__
    hip_version = getattr(torch.version, "hip", None)
    cuda_version = getattr(torch.version, "cuda", None)
    # A ROCm build reports hip and no cuda; both map onto torch.cuda.* APIs.
    report["runtime"] = "hip" if hip_version else ("cuda" if cuda_version else "cpu")
    if hip_version:
        report["hip"] = hip_version
    if cuda_version:
        report["cuda"] = cuda_version

    try:
        if torch.cuda.is_available():
            properties = torch.cuda.get_device_properties(0)
            report["device"] = properties.name
            report["vram_gb"] = round(properties.total_memory / 1024**3, 1)
            if report["vram_gb"] and report["vram_gb"] < VRAM_OFFLOAD_THRESHOLD / 1024**3:
                report["offload"] = "model-cpu-offload"
                report["warnings"].append(
                    f"Only {report['vram_gb']} GB of VRAM, so weights stream from system RAM during generation."
                )
        else:
            report["device"] = "cpu"
            report["vram_gb"] = 0
    except Exception as error:  # pragma: no cover - defensive
        report["device"] = f"unknown ({error})"

    if report["runtime"] == "cpu":
        report["warnings"].append(
            "This PyTorch build has no GPU runtime, so generation falls back to the CPU and is very slow. "
            "On an AMD GPU install a ROCm build, on an NVIDIA GPU install a CUDA build."
        )
    if not report["weights_cached"]:
        report["warnings"].append(f"Model weights for {model} are not downloaded yet.")
    return report


def load_pipeline(model: str):
    import torch  # noqa: PLC0415
    from diffusers import DiffusionPipeline  # noqa: PLC0415

    # ROCm builds report hip here and still expose the torch.cuda API.
    backend = "hip" if getattr(torch.version, "hip", None) else "cuda"
    if not torch.cuda.is_available():
        return DiffusionPipeline.from_pretrained(
            model, torch_dtype=torch.float32, use_safetensors=True
        ), "cpu"

    pipeline = DiffusionPipeline.from_pretrained(
        model, torch_dtype=torch.float16, variant="fp16", use_safetensors=True
    )
    try:
        total_vram = torch.cuda.get_device_properties(0).total_memory
    except Exception:
        total_vram = 0
    if total_vram and total_vram < VRAM_OFFLOAD_THRESHOLD:
        # Keep the bulk of the weights in system RAM so a 6 GB card still works.
        pipeline.enable_model_cpu_offload()
        return pipeline, f"{backend}-offload"
    pipeline.to("cuda")
    return pipeline, backend


def state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "omarchy-assistant"


@contextmanager
def gpu_lock(timeout: float = 900.0):
    """Serialise local generation so two requests never both hold the model.

    A 6 GB card can only host one copy of the weights, so a second concurrent
    request waits here instead of crashing the first one with an OOM error.
    """
    directory = state_dir()
    directory.mkdir(parents=True, exist_ok=True)
    handle = os.open(directory / "image-local.lock", os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("another local image generation is still running")
                time.sleep(1.0)
        yield
    finally:
        if acquired:
            fcntl.flock(handle, fcntl.LOCK_UN)
        os.close(handle)


def generate(request: dict) -> None:
    try:
        prompt, aspect_ratio, steps, seed, model = validate(request)
    except ValueError as error:
        fail(str(error))
        return

    width, height = sizes_for(aspect_ratio)
    try:
        import torch  # noqa: PLC0415
    except Exception as error:
        fail(
            "The local image backend is not installed. Run bin/assistant-config setup-image-models first. "
            f"({error})"
        )
        return

    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True
    if not torch.cuda.is_available():
        backend = "ROCm" if getattr(torch.version, "hip", None) else "CUDA"
        fail(
            f"The local image backend needs a {backend}-enabled PyTorch build, and this one reports no GPU. "
            "Reinstall it with bin/assistant-config setup-image-models, or use the gemini backend instead."
        )
        return

    started = time.monotonic()
    try:
        with gpu_lock():
            pipeline, device = load_pipeline(model)
            load_seconds = round(time.monotonic() - started, 2)

            pipeline.set_progress_bar_config(disable=True)
            generator = torch.Generator(device="cpu").manual_seed(seed)
            generate_started = time.monotonic()
            try:
                with torch.inference_mode():
                    image = pipeline(
                        prompt=prompt,
                        num_inference_steps=steps,
                        guidance_scale=0.0,
                        width=width,
                        height=height,
                        generator=generator,
                    ).images[0]
            except torch.cuda.OutOfMemoryError:
                fail("The local image model ran out of VRAM at this size. Try 1:1 or fewer steps.")
                return
            except Exception as error:
                fail(f"Local image generation failed: {error}")
                return
            generate_seconds = round(time.monotonic() - generate_started, 2)
    except TimeoutError:
        fail("Another local image generation is still running. Try again in a moment.")
        return
    except Exception as error:
        fail(f"Local image generation failed: {error}")
        return

    handle, path = tempfile.mkstemp(prefix="omarchy-assistant-image-", suffix=".png")
    os.close(handle)
    try:
        image.save(path, format="PNG")
    except Exception as error:
        Path(path).unlink(missing_ok=True)
        fail(f"Could not write the generated image: {error}")
        return

    emit(
        {
            "ok": True,
            "backend": "local",
            "path": path,
            "mime": "image/png",
            "width": width,
            "height": height,
            "steps": steps,
            "seed": seed,
            "model": model,
            "device": device,
            "load_seconds": load_seconds,
            "generate_seconds": generate_seconds,
        }
    )


def selftest() -> None:
    failures: list[str] = []

    for aspect_ratio, (width, height) in ASPECT_SIZES.items():
        if width % 8 or height % 8:
            failures.append(f"{aspect_ratio} size {width}x{height} is not a multiple of 8")
        pixels = width * height
        if pixels > 1_100_000:
            failures.append(f"{aspect_ratio} size {width}x{height} is {pixels} pixels, too large for 6 GB cards")
    missing = [ratio for ratio in ("1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9") if ratio not in ASPECT_SIZES]
    if missing:
        failures.append("missing aspect ratios: " + ", ".join(missing))

    checks = [
        ({"prompt": "a red square"}, ("a red square", "1:1", DEFAULT_STEPS)),
        ({"prompt": "a red square", "aspect_ratio": "16:9", "steps": 4}, ("a red square", "16:9", 4)),
        ({"prompt": "a red square", "steps": 99}, ("a red square", "1:1", MAX_STEPS)),
    ]
    for request, expected in checks:
        prompt, aspect_ratio, steps, _seed, _model = validate(request)
        if (prompt, aspect_ratio, steps) != expected:
            failures.append(f"validate({request}) returned {(prompt, aspect_ratio, steps)}, expected {expected}")

    for request in ({"prompt": "no"}, {"prompt": "valid prompt", "aspect_ratio": "5:2"}):
        try:
            validate(request)
        except ValueError:
            continue
        failures.append(f"validate({request}) should have raised ValueError")

    seeded = validate({"prompt": "valid prompt", "seed": 1234})[3]
    if seeded != 1234:
        failures.append("explicit seed was not preserved")
    if validate({"prompt": "valid prompt"})[3] == validate({"prompt": "valid prompt"})[3]:
        failures.append("random seeds are not random")

    if failures:
        fail("selftest failed: " + "; ".join(failures))
        return
    emit({"ok": True, "selftest": True, "aspect_ratios": len(ASPECT_SIZES)})


def prefetch(model: str) -> None:
    """Download the model weights without keeping them resident on the GPU."""
    try:
        import torch  # noqa: PLC0415
        from diffusers import DiffusionPipeline  # noqa: PLC0415
    except Exception as error:
        fail(f"Dependencies are missing: {error}")
        return
    started = time.monotonic()
    try:
        DiffusionPipeline.from_pretrained(
            model,
            torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
            variant="fp16" if torch.cuda.is_available() else None,
            use_safetensors=True,
        )
    except Exception as error:
        fail(f"Could not download {model}: {error}")
        return
    emit(
        {
            "ok": True,
            "backend": "local",
            "model": model,
            "hf_home": str(HF_HOME),
            "download_seconds": round(time.monotonic() - started, 2),
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("generate", "doctor", "selftest", "prefetch"), nargs="?", default="generate"
    )
    parser.add_argument("--model", default=None, help="Hugging Face model id to use for the local backend")
    arguments = parser.parse_args()

    if arguments.command == "selftest":
        selftest()
        return 0
    if arguments.command == "doctor":
        emit({"ok": True, **runtime_report(arguments.model or DEFAULT_MODEL)})
        return 0
    if arguments.command == "prefetch":
        prefetch(arguments.model or os.environ.get("ASSISTANT_IMAGE_LOCAL_MODEL") or DEFAULT_MODEL)
        return 0

    raw = sys.stdin.read()
    try:
        request = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as error:
        fail(f"request was not valid JSON: {error}")
        return 1
    if not isinstance(request, dict):
        fail("request must be a JSON object")
        return 1
    generate(request)
    return 0


if __name__ == "__main__":
    sys.exit(main())
