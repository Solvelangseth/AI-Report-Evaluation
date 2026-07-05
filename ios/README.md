# ReportCapture — iOS capture client

A thin native capture app for the inspector: record voice, snap **categorized**
photos (auto-stamped with their offset into the recording), then upload the whole
session to the Flask backend, which transcribes, extracts findings, composes and
QA-checks a draft. **Deep review/editing happens in the web app** — the phone is
for capture only.

> ⚠️ **Scaffold.** These Swift sources are a working starting point but have **not
> been compiled** here (no Xcode in the build environment). Open them in Xcode,
> wire up an app target, and iterate. The networking layer mirrors the live API
> and is the most reusable part.

## What's here

| File | Role |
|------|------|
| `ReportCaptureApp.swift` | App entry point |
| `ContentView.swift` | The capture screen (record, category picker, camera, finish) |
| `CaptureModel.swift` | Orchestrates record → snap → upload → finalize → poll |
| `AudioRecorder.swift` | One continuous `.m4a` voice track + elapsed clock |
| `CameraView.swift` | Camera capture → JPEG (UIImagePickerController wrapper) |
| `APIClient.swift` | Async/await client for the capture API |
| `MultipartBody.swift` | Dependency-free multipart/form-data builder |
| `Models.swift` | `Category`, `SessionStatus`, `CapturedPhoto`, `AppConfig` |

## Setup

1. **Create the Xcode project**: New → App (SwiftUI, iOS 16+), product name
   `ReportCapture`. Delete the generated `ContentView.swift`/`App.swift` and add
   the files from `ReportCapture/` to the target.
2. **Info.plist permissions** (required or the app crashes on first use):
   - `NSMicrophoneUsageDescription` — "Brukes til å ta opp befaringen."
   - `NSCameraUsageDescription` — "Brukes til å ta bilder under befaringen."
3. **Point at your server** in `Models.swift` → `AppConfig`:
   - `baseURL` = your Flask host (e.g. `http://<your-mac-ip>:5050` for a device on
     the same network; `http://localhost:5050` only works in the simulator).
   - `token` = your `CAPTURE_API_TOKEN` (leave `nil` for an open dev server).
   - For plain-HTTP dev, add an ATS exception (`NSAppTransportSecurity` →
     `NSAllowsArbitraryLoads`), or serve the API over HTTPS.
4. Run on a **real device** (the camera needs hardware; the simulator has no camera).

## Flow

```
Start opptak → (pick category) → Ta bilde ×N → Fullfør og last opp
   → POST /api/sessions → upload audio + each photo → POST /finalize {background}
   → poll GET /api/sessions/{id} → "Utkast klart"  → open the web app to review
```

## Known gaps / next steps

- **Offline-first queue**: uploads currently require connectivity at "Fullfør".
  Persist `CapturedPhoto`s + the audio file and retry the upload queue when the
  network returns (basements have no signal). This is the most important hardening.
- **Custom camera UI**: swap `UIImagePickerController` for an `AVCaptureSession`
  shutter if you want burst capture or an in-frame category overlay.
- **Auth**: token is static here; add real auth if multiple inspectors.
- **Video mode**: not implemented (voice + photos first, per the plan).
