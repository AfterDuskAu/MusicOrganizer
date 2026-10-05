import CoreAudio
import Foundation

/// Where the Mac's sound is going, as Core Audio tells it. The app never chooses the
/// Mac's output. The one thing it ever changes is the output's mute, for a moment, when a
/// song is paused on an output with a long delay (`PauseSilence`).
enum SoundOutput {
    /// The output the Mac is using, and how long sound waits in it before it's heard, in
    /// seconds: the device's own delay, its safety margin, its buffer and its stream's
    /// delay. Measured on 2026-10-05: 0.026 for the iMac's speakers, 2.012 for an Apple
    /// TV over AirPlay. Nil if the Mac has no output or Core Audio won't say.
    static func current() -> (device: AudioObjectID, name: String, delay: Double)? {
        guard
            let device: AudioObjectID = value(
                of: AudioObjectID(kAudioObjectSystemObject),
                kAudioHardwarePropertyDefaultOutputDevice, kAudioObjectPropertyScopeGlobal),
            device != kAudioObjectUnknown,
            let rate: Double = value(
                of: device, kAudioDevicePropertyNominalSampleRate, kAudioObjectPropertyScopeGlobal),
            rate > 0
        else { return nil }
        let output = kAudioObjectPropertyScopeOutput
        var frames: UInt32 = value(of: device, kAudioDevicePropertyLatency, output) ?? 0
        frames += value(of: device, kAudioDevicePropertySafetyOffset, output) ?? 0
        frames += value(
            of: device, kAudioDevicePropertyBufferFrameSize, kAudioObjectPropertyScopeGlobal) ?? 0
        if let stream = firstOutputStream(of: device) {
            frames += value(
                of: stream, kAudioStreamPropertyLatency, kAudioObjectPropertyScopeGlobal) ?? 0
        }
        return (device, name(of: device) ?? "this output", Double(frames) / rate)
    }

    /// Whether an output is muted, as the Mac's own mute key would leave it. Nil if the
    /// output has no mute.
    static func isMuted(_ device: AudioObjectID) -> Bool? {
        let muted: UInt32? = value(
            of: device, kAudioDevicePropertyMute, kAudioObjectPropertyScopeOutput)
        return muted.map { $0 != 0 }
    }

    /// Mutes or unmutes an output, as the Mac's own mute key does. False if it wouldn't.
    static func setMuted(_ muted: Bool, on device: AudioObjectID) -> Bool {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyMute, mScope: kAudioObjectPropertyScopeOutput,
            mElement: kAudioObjectPropertyElementMain)
        var value: UInt32 = muted ? 1 : 0
        return AudioObjectSetPropertyData(
            device, &address, 0, nil, UInt32(MemoryLayout<UInt32>.size), &value) == noErr
    }

    /// Calls `changed` on the main thread whenever the Mac's sound goes to a different
    /// output. It's asked for once, and kept for as long as the app runs.
    static func onChange(_ changed: @escaping @MainActor () -> Void) {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultOutputDevice,
            mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        AudioObjectAddPropertyListenerBlock(
            AudioObjectID(kAudioObjectSystemObject), &address, DispatchQueue.main
        ) { _, _ in
            MainActor.assumeIsolated { changed() }
        }
    }

    /// One plain value of an audio object (a number, or another object's id).
    private static func value<Value>(
        of object: AudioObjectID, _ selector: AudioObjectPropertySelector,
        _ scope: AudioObjectPropertyScope
    ) -> Value? {
        var address = AudioObjectPropertyAddress(
            mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
        var size = UInt32(MemoryLayout<Value>.size)
        let found = UnsafeMutablePointer<Value>.allocate(capacity: 1)
        defer { found.deallocate() }
        guard AudioObjectGetPropertyData(object, &address, 0, nil, &size, found) == noErr,
            size == UInt32(MemoryLayout<Value>.size)
        else { return nil }
        return found.pointee
    }

    private static func name(of device: AudioObjectID) -> String? {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioObjectPropertyName, mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        var found: Unmanaged<CFString>?
        guard AudioObjectGetPropertyData(device, &address, 0, nil, &size, &found) == noErr,
            let found
        else { return nil }
        return found.takeRetainedValue() as String
    }

    private static func firstOutputStream(of device: AudioObjectID) -> AudioObjectID? {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyStreams, mScope: kAudioObjectPropertyScopeOutput,
            mElement: kAudioObjectPropertyElementMain)
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(device, &address, 0, nil, &size) == noErr,
            size >= UInt32(MemoryLayout<AudioObjectID>.size)
        else { return nil }
        var streams = [AudioObjectID](
            repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        guard AudioObjectGetPropertyData(device, &address, 0, nil, &size, &streams) == noErr
        else { return nil }
        return streams.first
    }
}
