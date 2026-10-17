// Draws Bear Media's icon from the bear the app itself draws (BearDrawing.swift): the
// owner's logo of 2026-10-17, a bear holding a ticket with a play button on a night sky,
// in a macOS icon's rounded square. scripts/make_icon.sh compiles this with that file and
// makes app/Resources/AppIcon.icns.
//
//   make_icon <folder>             the icon's pictures, every size an .iconset has
//   make_icon <folder> <seconds>…  pictures of the loading screen at those moments
import AppKit
import SwiftUI

/// The icon: the sky and the bear in Apple's rounded square, with room around it.
struct Icon: View {
    var body: some View {
        ZStack {
            BearScene(still: true, bearSize: 1)
                .frame(width: 824, height: 824)
                .clipShape(RoundedRectangle(cornerRadius: 185, style: .continuous))
                .shadow(color: .black.opacity(0.35), radius: 12, y: 8)
        }
        .frame(width: 1024, height: 1024)
    }
}

@MainActor
func save(_ view: some View, to file: String, size: CGSize, scale: CGFloat = 1) {
    let renderer = ImageRenderer(content: view.frame(width: size.width, height: size.height))
    renderer.scale = scale
    guard let image = renderer.cgImage else { fatalError("Nothing was drawn for \(file)") }
    let bitmap = NSBitmapImageRep(cgImage: image)
    try! bitmap.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: file))
}

@main
enum MakeIcon {
    @MainActor
    static func main() {
        let words = Array(CommandLine.arguments.dropFirst())
        let folder = words.first ?? "."
        if words.count > 1 {
            for moment in words.dropFirst().compactMap(Double.init) {
                save(BearScene(moment: moment), to: "\(folder)/scene-\(moment).png", size: CGSize(width: 1280, height: 800))
            }
            return
        }
        // An .iconset's pictures: each size, and each at twice the size.
        for points in [16, 32, 128, 256, 512] {
            for scale in [1, 2] {
                let name = scale == 1 ? "icon_\(points)x\(points).png" : "icon_\(points)x\(points)@2x.png"
                let pixels = CGFloat(points * scale)
                save(Icon().scaleEffect(pixels / 1024), to: "\(folder)/\(name)", size: CGSize(width: pixels, height: pixels))
            }
        }
    }
}
