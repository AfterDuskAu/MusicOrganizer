import XCTest

@testable import MusicOrganizerKit

/// The browser window (2026-10-18): a page's videos found wherever they're written, and a
/// stream's list of parts read, HLS and DASH, with a locked or live one refused.
final class BrowserFindingTests: XCTestCase {
    private let page = URL(string: "https://www.example.test/watch/one")!

    // MARK: finding addresses

    func testAddressesAreFoundHoweverThePageWritesThem() {
        let text = #"""
            <video src="/clip.mp4"></video>
            {"hls":"https:\/\/cdn.example.test\/v\/master.m3u8?token=a&b=c","x":1}
            var s = "https://cdn.example.test/d/manifest.mpd";
            <a href="https://files.example.test/film.webm">get</a>
            encoded=https%3A%2F%2Fcdn.example.test%2Fe%2Fwhole.mp4&next=1
            <img src="https://cdn.example.test/poster.jpg">
            https://cdn.example.test/v/seg-00012.ts https://cdn.example.test/v/chunk_3.m4s
            https://cdn.example.test/fake.mp4a
            """#
        let found = MediaLinks.find(in: text, base: page).map(\.absoluteString)
        XCTAssertEqual(
            found,
            [
                "https://cdn.example.test/v/master.m3u8?token=a&b=c",
                "https://cdn.example.test/d/manifest.mpd",
                "https://files.example.test/film.webm",
                "https://cdn.example.test/e/whole.mp4",
            ])
    }

    func testWhatAnAddressIs() {
        func kind(_ text: String, _ type: String? = nil) -> MediaKind? {
            MediaLinks.kind(of: URL(string: text)!, contentType: type)
        }
        XCTAssertEqual(kind("https://a.test/x/index.m3u8"), .hls)
        XCTAssertEqual(kind("https://a.test/play?id=4", "application/vnd.apple.mpegurl"), .hls)
        XCTAssertEqual(kind("https://a.test/m.mpd"), .dash)
        XCTAssertEqual(kind("https://a.test/get?f=1", "application/dash+xml"), .dash)
        XCTAssertEqual(kind("https://a.test/film.MP4"), .file)
        XCTAssertEqual(kind("https://a.test/blob", "video/mp4"), .file)
        XCTAssertNil(kind("https://a.test/page.html"))
        XCTAssertNil(kind("blob:https://a.test/1234"))
        XCTAssertTrue(MediaLinks.isPart(URL(string: "https://a.test/v/seg-1.ts")!))
        XCTAssertTrue(MediaLinks.isPart(URL(string: "https://a.test/v/chunk-stream0-00001.m4s")!))
        XCTAssertTrue(MediaLinks.isPart(URL(string: "https://a.test/v/frag_12.mp4")!))
        XCTAssertTrue(MediaLinks.isPart(URL(string: "https://a.test/v/file.mp4?range=0-1000")!))
        XCTAssertFalse(MediaLinks.isPart(URL(string: "https://a.test/v/film-1080.mp4")!))
    }

    func testFindingsAreOneEntryAnAddressStreamsFirstAndPartsLeftOut() {
        var findings = PageFindings()
        let frame = page.absoluteString
        findings.add(
            [
                ("https://cdn.example.test/a/preview.mp4", "the player", nil),
                ("https://cdn.example.test/a/master.m3u8", "the page's requests", "application/x-mpegURL"),
                ("https://cdn.example.test/a/master.m3u8", "the page's code", nil),
                ("https://cdn.example.test/a/seg001.ts", "the page's requests", "video/mp2t"),
                ("https://cdn.example.test/a/720/index.m3u8", "the page's requests", nil),
            ], frame: frame)
        // Many numbered files in one folder are a stream's parts, however they're named.
        findings.add((1...8).map { ("https://cdn.example.test/b/piece\($0).mp4", "the page's requests", nil) }, frame: frame)
        XCTAssertEqual(
            findings.media.map(\.url.lastPathComponent), ["master.m3u8", "index.m3u8", "preview.mp4"])
        XCTAssertEqual(findings.media[0].found, ["the page's requests", "the page's code"])
        XCTAssertEqual(findings.media[0].frame, frame)
        // A size's own list, read as part of its stream, isn't listed apart, now or later.
        findings.leaveOut(["https://cdn.example.test/a/720/index.m3u8"])
        findings.add([("https://cdn.example.test/a/720/index.m3u8", "the player", nil)], frame: frame)
        XCTAssertEqual(findings.media.map(\.url.lastPathComponent), ["master.m3u8", "preview.mp4"])
        XCTAssertFalse(findings.protected)
        findings.note(protected: true)
        XCTAssertTrue(findings.protected)
    }

    // MARK: HLS

    func testAnHLSMasterListsItsSizesSharpestFirstWithTheirSound() throws {
        let master = """
            #EXTM3U
            #EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",DEFAULT=YES,URI="audio/en.m3u8"
            #EXT-X-STREAM-INF:BANDWIDTH=600000,RESOLUTION=640x360,CODECS="avc1.64001e,mp4a.40.2",AUDIO="aud"
            360/index.m3u8
            #EXT-X-STREAM-INF:BANDWIDTH=2500000,AVERAGE-BANDWIDTH=2000000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2",AUDIO="aud"
            https://other.example.test/1080/index.m3u8
            #EXT-X-STREAM-INF:BANDWIDTH=64000,CODECS="mp4a.40.2"
            sound-only.m3u8
            """
        let base = URL(string: "https://cdn.example.test/v/master.m3u8")!
        guard case .sizes(let sizes) = try HLSList.read(master, base: base) else {
            return XCTFail("not read as a master list")
        }
        XCTAssertEqual(sizes.map(\.height), [1080, 360])
        XCTAssertEqual(sizes[0].list?.absoluteString, "https://other.example.test/1080/index.m3u8")
        XCTAssertEqual(sizes[1].list?.absoluteString, "https://cdn.example.test/v/360/index.m3u8")
        XCTAssertEqual(sizes[0].audioList?.absoluteString, "https://cdn.example.test/v/audio/en.m3u8")
        XCTAssertEqual(sizes[0].bandwidth, 2_000_000)
        var timed = sizes[0]
        timed.seconds = 600
        XCTAssertEqual(timed.label, "1080p · about 150 MB")
    }

    func testAnHLSListsPartsWithItsHeaderAndRanges() throws {
        let list = """
            #EXTM3U
            #EXT-X-VERSION:7
            #EXT-X-TARGETDURATION:6
            #EXT-X-MAP:URI="init.mp4",BYTERANGE="720@0"
            #EXTINF:6.0,
            #EXT-X-BYTERANGE:1000@720
            video.mp4
            #EXTINF:4.5,
            #EXT-X-BYTERANGE:500
            video.mp4
            #EXT-X-KEY:METHOD=NONE
            #EXTINF:2,
            last.m4s
            #EXT-X-ENDLIST
            """
        let base = URL(string: "https://cdn.example.test/v/720/index.m3u8")!
        guard case .parts(let parts) = try HLSList.read(list, base: base) else {
            return XCTFail("not read as a media list")
        }
        XCTAssertEqual(parts.header, StreamPart(url: URL(string: "https://cdn.example.test/v/720/init.mp4")!, range: 0...719))
        XCTAssertEqual(parts.parts.map(\.range), [720...1719, 1720...2219, nil])
        XCTAssertEqual(parts.all.count, 4)
        XCTAssertEqual(parts.seconds, 12.5, accuracy: 0.001)
    }

    func testALockedOrLiveHLSIsNeverSaved() {
        let base = URL(string: "https://cdn.example.test/v/index.m3u8")!
        let locked = "#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=\"key.bin\"\n#EXTINF:6,\na.ts\n#EXT-X-ENDLIST\n"
        XCTAssertThrowsError(try HLSList.read(locked, base: base)) { XCTAssertEqual($0 as? StreamListError, .locked) }
        let fairplay = "#EXTM3U\n#EXT-X-SESSION-KEY:METHOD=SAMPLE-AES,URI=\"skd://x\"\n#EXT-X-STREAM-INF:BANDWIDTH=1\na.m3u8\n"
        XCTAssertThrowsError(try HLSList.read(fairplay, base: base)) { XCTAssertEqual($0 as? StreamListError, .locked) }
        let live = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\na.ts\n"
        XCTAssertThrowsError(try HLSList.read(live, base: base)) { XCTAssertEqual($0 as? StreamListError, .live) }
        let vod = "#EXTM3U\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXTINF:6,\na.ts\n"
        XCTAssertNoThrow(try HLSList.read(vod, base: base))
        XCTAssertThrowsError(try HLSList.read("<html>", base: base)) { XCTAssertEqual($0 as? StreamListError, .notAList) }
        XCTAssertEqual(HLSList.attributes(#"#X:A=1,B="two, three",C=x"#), ["A": "1", "B": "two, three", "C": "x"])
    }

    // MARK: DASH

    func testADASHTemplateWithATimelineAndSoundApart() throws {
        let manifest = """
            <?xml version="1.0"?>
            <MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT0H0M8.0S">
              <Period>
                <BaseURL>media/</BaseURL>
                <AdaptationSet mimeType="video/mp4">
                  <SegmentTemplate timescale="1000" initialization="init-$RepresentationID$.m4s" media="chunk-$RepresentationID$-$Number%05d$.m4s" startNumber="1">
                    <SegmentTimeline><S t="0" d="4000" r="1"/></SegmentTimeline>
                  </SegmentTemplate>
                  <Representation id="v1" bandwidth="800000" width="1280" height="720" codecs="avc1.64001f"/>
                  <Representation id="v0" bandwidth="300000" width="640" height="360" codecs="avc1.64001e"/>
                </AdaptationSet>
                <AdaptationSet mimeType="audio/mp4">
                  <Representation id="a0" bandwidth="128000" codecs="mp4a.40.2">
                    <SegmentTemplate timescale="48000" duration="192000" initialization="a-init.m4s" media="a-$Time$-$Number$.m4s"/>
                  </Representation>
                </AdaptationSet>
              </Period>
            </MPD>
            """
        let base = URL(string: "https://cdn.example.test/d/manifest.mpd")!
        let choices = try DASHList.read(manifest, base: base)
        XCTAssertEqual(choices.map(\.height), [720, 360])
        XCTAssertEqual(
            choices[0].video.map(\.url.lastPathComponent),
            ["init-v1.m4s", "chunk-v1-00001.m4s", "chunk-v1-00002.m4s"])
        XCTAssertEqual(choices[0].video[0].url.absoluteString, "https://cdn.example.test/d/media/init-v1.m4s")
        XCTAssertEqual(choices[0].audio.map(\.url.lastPathComponent), ["a-init.m4s", "a--1.m4s", "a--2.m4s"])
        XCTAssertEqual(choices[0].seconds, 8)
        XCTAssertEqual(choices[0].bandwidth, 928_000)
    }

    func testADASHWholeFileAndAListOfParts() throws {
        let manifest = """
            <MPD type="static" mediaPresentationDuration="PT1M">
              <Period>
                <AdaptationSet contentType="video">
                  <Representation id="hd" bandwidth="2000000" height="1080" mimeType="video/mp4">
                    <BaseURL>https://files.example.test/hd.mp4</BaseURL>
                    <SegmentBase indexRange="800-1500"><Initialization range="0-799"/></SegmentBase>
                  </Representation>
                  <Representation id="sd" bandwidth="500000" height="480" mimeType="video/mp4">
                    <SegmentList><Initialization sourceURL="sd/init.mp4"/><SegmentURL media="sd/1.m4s"/><SegmentURL media="sd/2.m4s"/></SegmentList>
                  </Representation>
                </AdaptationSet>
              </Period>
              <Period><AdaptationSet mimeType="video/mp4"><Representation id="ad" height="2160"/></AdaptationSet></Period>
            </MPD>
            """
        let choices = try DASHList.read(manifest, base: URL(string: "https://cdn.example.test/m.mpd")!)
        XCTAssertEqual(choices.map(\.height), [1080, 480])  // the second period (an advert) isn't the video
        XCTAssertEqual(choices[0].video, [StreamPart(url: URL(string: "https://files.example.test/hd.mp4")!)])
        XCTAssertEqual(choices[1].video.map(\.url.path), ["/sd/init.mp4", "/sd/1.m4s", "/sd/2.m4s"])
        XCTAssertEqual(DASHList.seconds("PT1H2M3.5S"), 3723.5)
    }

    func testAProtectedOrLiveDASHIsNeverSaved() {
        let protected = """
            <MPD type="static"><Period><AdaptationSet mimeType="video/mp4">
              <ContentProtection schemeIdUri="urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"/>
              <Representation id="v" height="720"><BaseURL>v.mp4</BaseURL></Representation>
            </AdaptationSet></Period></MPD>
            """
        let base = URL(string: "https://cdn.example.test/m.mpd")!
        XCTAssertThrowsError(try DASHList.read(protected, base: base)) {
            XCTAssertEqual($0 as? StreamListError, .locked)
        }
        let live = #"<MPD type="dynamic"><Period/></MPD>"#
        XCTAssertThrowsError(try DASHList.read(live, base: base)) { XCTAssertEqual($0 as? StreamListError, .live) }
        XCTAssertThrowsError(try DASHList.read("not xml", base: base))
    }
}
