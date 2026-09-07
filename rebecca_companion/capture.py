from __future__ import annotations

import ctypes
import re
from ctypes import wintypes
from dataclasses import dataclass

import mss
from PIL import Image


class ScreenCaptureError(RuntimeError):
    pass


@dataclass
class CapturedScreen:
    title: str
    image: Image.Image
    backend: str


def active_window_info() -> tuple[str, tuple[int, int, int, int] | None]:
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        rect = wintypes.RECT()
        if not hwnd or not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return buffer.value or "Aplicación desconocida", None
        return buffer.value or "Aplicación desconocida", (
            rect.left,
            rect.top,
            rect.right,
            rect.bottom,
        )
    except Exception:
        return "Aplicación desconocida", None


def _monitor_for_window(monitors: list[dict], rect: tuple[int, int, int, int] | None) -> tuple[dict, int]:
    physical = monitors[1:] or monitors
    selected_index = 0
    selected = physical[0]
    if rect:
        center_x = (rect[0] + rect[2]) // 2
        center_y = (rect[1] + rect[3]) // 2
        for index, candidate in enumerate(physical):
            if (
                candidate["left"] <= center_x < candidate["left"] + candidate["width"]
                and candidate["top"] <= center_y < candidate["top"] + candidate["height"]
            ):
                return candidate, index
    return selected, selected_index


def _dxcam_candidates(preferred_output: int, prefer_primary: bool) -> list[tuple[int, int]]:
    import dxcam

    discovered: list[tuple[int, int, bool]] = []
    try:
        for device, output, primary in re.findall(
            r"Device\[(\d+)] Output\[(\d+)]:.*?Primary:(True|False)",
            dxcam.output_info(),
        ):
            discovered.append((int(device), int(output), primary == "True"))
    except Exception:
        pass

    preferred: list[tuple[int, int]] = []
    if prefer_primary:
        preferred.extend((device, output) for device, output, primary in discovered if primary)
    preferred.append((0, preferred_output))
    preferred.extend((device, output) for device, output, _primary in discovered)
    preferred.extend([(0, 0), (0, 1)])
    return list(dict.fromkeys(preferred))


def _capture_with_dxcam(preferred_output: int, prefer_primary: bool) -> Image.Image:
    try:
        import dxcam
    except ImportError as exc:
        raise ScreenCaptureError("Falta el capturador DirectX (dxcam).") from exc

    errors: list[str] = []
    for device_index, output_index in _dxcam_candidates(preferred_output, prefer_primary):
        camera = None
        try:
            camera = dxcam.create(
                device_idx=device_index,
                output_idx=output_index,
                output_color="RGB",
                processor_backend="numpy",
            )
            frame = camera.grab(new_frame_only=False)
            if frame is not None:
                return Image.fromarray(frame).convert("RGB")
        except Exception as exc:
            errors.append(str(exc))
        finally:
            if camera is not None:
                try:
                    camera.release()
                except Exception:
                    pass
    detail = next((error for error in errors if error), "ninguna pantalla disponible")
    raise ScreenCaptureError(f"DirectX tampoco pudo leer la pantalla: {detail}")


def _capture_all_with_dxcam(monitors: list[dict]) -> Image.Image:
    if not monitors:
        raise ScreenCaptureError("Windows no informó pantallas disponibles.")
    left = min(monitor["left"] for monitor in monitors)
    top = min(monitor["top"] for monitor in monitors)
    right = max(monitor["left"] + monitor["width"] for monitor in monitors)
    bottom = max(monitor["top"] + monitor["height"] for monitor in monitors)
    canvas = Image.new("RGB", (right - left, bottom - top), "black")
    capturadas = 0
    errores: list[str] = []
    for index, monitor in enumerate(monitors):
        try:
            imagen = _capture_with_dxcam(
                index,
                monitor.get("left") == 0 and monitor.get("top") == 0,
            )
            if imagen.size != (monitor["width"], monitor["height"]):
                imagen = imagen.resize((monitor["width"], monitor["height"]))
            canvas.paste(imagen, (monitor["left"] - left, monitor["top"] - top))
            capturadas += 1
        except Exception as exc:
            errores.append(str(exc))
    if not capturadas:
        detalle = next((error for error in errores if error), "ninguna pantalla disponible")
        raise ScreenCaptureError(f"DirectX tampoco pudo leer las pantallas: {detalle}")
    return canvas


def capture_monitor(
    monitor_number: int | None = 1,
    *,
    combined: bool = False,
    max_size: tuple[int, int] | None = None,
) -> CapturedScreen:
    """Captura un monitor concreto y cae a DirectX si BitBlt/MSS es rechazado."""
    classic_error: Exception | None = None
    try:
        with mss.mss() as capture:
            physical = capture.monitors[1:] or capture.monitors
            if not physical:
                raise ScreenCaptureError("Windows no informó pantallas disponibles.")
            if combined:
                monitor = capture.monitors[0]
                title = "Todas las pantallas"
                try:
                    shot = capture.grab(monitor)
                    image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    backend = "mss"
                except Exception as exc:
                    classic_error = exc
                    image = _capture_all_with_dxcam(physical)
                    backend = "directx"
            else:
                requested = max(1, int(monitor_number or 1))
                selected_index = min(requested - 1, len(physical) - 1)
                monitor = physical[selected_index]
                title = f"Pantalla {selected_index + 1}"
                try:
                    shot = capture.grab(monitor)
                    image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    backend = "mss"
                except Exception as exc:
                    classic_error = exc
                    image = _capture_with_dxcam(
                        selected_index,
                        monitor.get("left") == 0 and monitor.get("top") == 0,
                    )
                    backend = "directx"
    except ScreenCaptureError:
        raise
    except Exception as exc:
        classic_error = classic_error or exc
        if combined:
            raise ScreenCaptureError(f"No pude enumerar las pantallas: {classic_error}") from exc
        image = _capture_with_dxcam(max(0, int(monitor_number or 1) - 1), monitor_number in {None, 1})
        title = f"Pantalla {max(1, int(monitor_number or 1))}"
        backend = "directx"

    if max_size:
        image.thumbnail(max_size)
    if image.width < 2 or image.height < 2:
        raise ScreenCaptureError("La captura de pantalla llegó vacía.")
    return CapturedScreen(title=title, image=image, backend=backend)


def capture_active_monitor(max_size: tuple[int, int] | None = None) -> CapturedScreen:
    title, rect = active_window_info()
    classic_error: Exception | None = None
    preferred_output = 0
    prefer_primary = True

    try:
        with mss.mss() as capture:
            if "rebecca companion" in title.lower():
                physical = capture.monitors[1:] or capture.monitors
                monitor = next(
                    (
                        candidate
                        for candidate in physical
                        if candidate.get("left") == 0 and candidate.get("top") == 0
                    ),
                    physical[0],
                )
                preferred_output = physical.index(monitor)
                title = "Pantalla principal (Rebecca fue ignorada)"
            else:
                monitor, preferred_output = _monitor_for_window(capture.monitors, rect)
            prefer_primary = monitor.get("left") == 0 and monitor.get("top") == 0
            try:
                shot = capture.grab(monitor)
                image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                backend = "mss"
            except Exception as exc:
                classic_error = exc
                image = _capture_with_dxcam(preferred_output, prefer_primary)
                backend = "directx"
    except ScreenCaptureError:
        raise
    except Exception as exc:
        classic_error = classic_error or exc
        image = _capture_with_dxcam(preferred_output, prefer_primary)
        backend = "directx"

    if max_size:
        image.thumbnail(max_size)
    if image.width < 2 or image.height < 2:
        raise ScreenCaptureError("La captura de pantalla llegó vacía.")
    return CapturedScreen(title=title, image=image, backend=backend)
