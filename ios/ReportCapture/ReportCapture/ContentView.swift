import SwiftUI

/// The one capture screen: title, record toggle, a category picker, a big
/// camera button, captured thumbnails, and a finish/upload action.
struct ContentView: View {
    @StateObject private var model = CaptureModel()
    @State private var showCamera = false

    var body: some View {
        NavigationStack {
            Group {
                switch model.phase {
                case .uploading, .processing, .done, .failed:
                    resultView
                default:
                    captureView
                }
            }
            .navigationTitle("Befaring")
            .task { await model.loadCategories() }
        }
    }

    // MARK: - Capture

    private var captureView: some View {
        VStack(spacing: 16) {
            TextField("Tittel (adresse)", text: $model.title)
                .textFieldStyle(.roundedBorder)
                .padding(.horizontal)

            recordButton

            categoryPicker

            Button {
                showCamera = true
            } label: {
                Label("Ta bilde", systemImage: "camera.fill")
                    .font(.title2.bold())
                    .frame(maxWidth: .infinity).padding()
                    .background(model.recorder.isRecording ? Color.blue : Color.gray.opacity(0.4))
                    .foregroundColor(.white).cornerRadius(14)
            }
            .disabled(!model.recorder.isRecording || model.selectedCategory == nil)
            .padding(.horizontal)
            .fullScreenCover(isPresented: $showCamera) {
                CameraView { jpeg in model.addPhoto(jpeg) }.ignoresSafeArea()
            }

            thumbnails

            Spacer()

            Button {
                Task { await model.finishAndUpload() }
            } label: {
                Text("Fullfør og last opp")
                    .font(.headline).frame(maxWidth: .infinity).padding()
                    .background(Color.green).foregroundColor(.white).cornerRadius(14)
            }
            .disabled(model.photos.isEmpty && !model.recorder.isRecording)
            .padding()
        }
    }

    private var recordButton: some View {
        Button {
            model.recorder.isRecording ? model.recorder.stop() : model.startRecording()
        } label: {
            HStack {
                Image(systemName: model.recorder.isRecording ? "stop.circle.fill" : "mic.circle.fill")
                Text(model.recorder.isRecording
                     ? String(format: "Tar opp  %.0f s", model.recorder.elapsed)
                     : "Start opptak")
            }
            .font(.title3.bold())
            .foregroundColor(model.recorder.isRecording ? .red : .blue)
        }
    }

    private var categoryPicker: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack {
                ForEach(model.categories) { cat in
                    let selected = model.selectedCategory == cat
                    Text(cat.label)
                        .font(.subheadline)
                        .padding(.horizontal, 12).padding(.vertical, 8)
                        .background(selected ? Color.blue : Color.gray.opacity(0.15))
                        .foregroundColor(selected ? .white : .primary)
                        .cornerRadius(20)
                        .onTapGesture { model.selectedCategory = cat }
                }
            }.padding(.horizontal)
        }
    }

    private var thumbnails: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack {
                ForEach(model.photos) { photo in
                    VStack(spacing: 4) {
                        if let ui = UIImage(data: photo.jpeg) {
                            Image(uiImage: ui).resizable().scaledToFill()
                                .frame(width: 70, height: 70).clipped().cornerRadius(8)
                        }
                        Text(photo.category.label).font(.caption2).lineLimit(1)
                        Text(String(format: "%.0fs", photo.timestamp))
                            .font(.caption2).foregroundColor(.secondary)
                    }.frame(width: 76)
                }
            }.padding(.horizontal)
        }
    }

    // MARK: - Result

    private var resultView: some View {
        VStack(spacing: 20) {
            switch model.phase {
            case .uploading:
                ProgressView("Laster opp…")
            case .processing:
                ProgressView("Analyserer befaringen…")
            case .done:
                Image(systemName: "checkmark.seal.fill").font(.system(size: 56)).foregroundColor(.green)
                Text("Utkast klart").font(.title2.bold())
                if let v = model.status?.verdict { Text("QA: \(v)").foregroundColor(.secondary) }
                Text("Åpne web-appen for å gå gjennom funnene og signere.")
                    .font(.footnote).foregroundColor(.secondary).multilineTextAlignment(.center)
            case .failed:
                Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 56)).foregroundColor(.orange)
                Text(model.errorMessage ?? "Noe gikk galt").multilineTextAlignment(.center)
            default:
                EmptyView()
            }
            Button("Ny befaring") { model.reset() }.padding(.top)
        }.padding()
    }
}

#Preview { ContentView() }
