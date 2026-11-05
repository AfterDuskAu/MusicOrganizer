import MusicOrganizerKit
import SwiftUI

/// Videos → Calendar, as the owner drew it (2026-10-15): the year over the month's name,
/// the months either side to go to, the days of the week from Monday, and the month's
/// days, today ringed. On each day: the episodes of the owner's series that come out
/// then (a series that's hearted, on the Watchlist, or been watched), and the videos the
/// channels they follow put up (asked for 2026-10-12: "It takes information from ❤️
/// items, or followed, and shows when the next episode comes out, or ... your favourite
/// channel uploads"). A click opens an episode on its series' page, or plays a video.
struct CalendarView: View {
    @Environment(AppModel.self) private var model
    @State private var month = MonthGrid(containing: Date())
    /// Every episode of the owner's series, by day: laid out again when more are read.
    @State private var episodes: [String: [CalendarEntry]] = [:]
    /// "watchlisted" (the owner's series and channels, as before) or "general" (2026-11-05:
    /// everything coming out, from TMDB, with the computer's key).
    @AppStorage("calendarShows") private var shows = "watchlisted"
    /// TMDB's releases, by month ("2026-11"), and why a month's couldn't be had.
    @State private var releases: [String: [TMDBRelease]] = [:]
    @State private var releasesProblem: String?

    private var general: Bool { shows == "general" && model.tmdbHasKey }
    private var monthKey: String { String(format: "%04d-%02d", month.year, month.month) }

    var body: some View {
        let uploads = general ? [:] : CalendarEntry.byDay(CalendarEntry.uploads(model.calendarUploads))
        let today = CalendarEntry.day(of: Date())
        let shown = general ? generalDays : episodes
        VStack(spacing: 0) {
            if model.tmdbHasKey {
                Picker("Show", selection: $shows) {
                    Text("Watchlisted").tag("watchlisted")
                    Text("General").tag("general")
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
                .padding(.top, 12)
                .help("Watchlisted: your series and channels. General: every film coming out in Australia, and new series, from TMDB.")
            }
            header
            if general, let releasesProblem {
                Text(releasesProblem).foregroundStyle(.secondary).padding(.bottom, 10)
            } else if !general, model.ownSeries.isEmpty, model.followedChannels.isEmpty {
                Text(
                    "Give a series a heart, put one on your Watchlist, or follow a channel, and its new "
                        + "episodes and videos show here on the day they come out.")
                    .foregroundStyle(.secondary)
                    .padding(.bottom, 10)
            }
            HStack(spacing: 1) {
                ForEach(MonthGrid.weekdays, id: \.self) { name in
                    Text(name)
                        .lineLimit(1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.leading, 14)
                }
            }
            .padding(.bottom, 10)
            VStack(spacing: 1) {
                ForEach(Array(month.weeks.enumerated()), id: \.offset) { _, week in
                    HStack(spacing: 1) {
                        ForEach(0..<7, id: \.self) { column in
                            let number = week[column]
                            let day = number.map(month.day)
                            DayCell(
                                number: number, isToday: day == today,
                                entries: day.map { (shown[$0] ?? []) + (uploads[$0] ?? []) } ?? [],
                                open: open)
                        }
                    }
                }
            }
            .frame(maxHeight: .infinity)
        }
        .padding(.horizontal, 12)
        .padding(.bottom, 12)
        .task(id: model.seriesRead) { await layOut() }
        .task(id: "\(monthKey)|\(general)") { if general { await readReleases() } }
        .task { await model.readSeries(model.ownSeries, episodes: true) }
        .task(id: model.followedChannels.map(\.channelId)) { await model.readUploads() }
    }

    /// The year over the month's name, with the months either side, as drawn.
    private var header: some View {
        HStack {
            Button {
                month = month.moved(by: -1)
            } label: {
                Label(month.moved(by: -1).shortName, systemImage: "chevron.left")
            }
            .help("The month before")
            Spacer()
            VStack(spacing: 2) {
                Text(String(month.year)).font(.title3).foregroundStyle(.secondary)
                Text(month.name).font(.system(size: 30, weight: .medium)).heading()
                    .foregroundStyle(.primary)
            }
            .onTapGesture(count: 2) { month = MonthGrid(containing: Date()) }
            .help("Double-click for this month")
            Spacer()
            if model.readingSeries || model.readingUploads || (general && releases[monthKey] == nil && releasesProblem == nil) {
                ProgressView().controlSize(.small)
                    .help("Looking at your series and channels")
            }
            Button {
                month = month.moved(by: 1)
            } label: {
                HStack(spacing: 6) {
                    Text(month.moved(by: 1).shortName)
                    Image(systemName: "chevron.right")
                }
            }
            .help("The month after")
        }
        .buttonStyle(.plain)
        .font(.title3)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 16)
        .padding(.top, 14)
        .padding(.bottom, 18)
    }

    /// Every episode put on its day, away from the screen's own work: with hundreds of
    /// series it was over a second the window couldn't answer (2026-10-17).
    private func layOut() async {
        let wanted = model.ownSeries.map { ($0, model.seriesEpisodes[$0.id] ?? []) }
        let laid = await Task.detached(priority: .userInitiated) {
            CalendarEntry.byDay(wanted.flatMap { CalendarEntry.episodes(of: $0.0, $0.1) })
        }.value
        guard !Task.isCancelled else { return }
        episodes = laid
    }

    /// The month's releases from TMDB, each on its day; in a child's profile kept to some
    /// ratings, only those rated so.
    private var generalDays: [String: [CalendarEntry]] {
        let rules = model.titleRules(language: OriginalLanguage.all)
        let found = (releases[monthKey] ?? []).filter { model.titles.shows($0.item, rules, languageToo: false) }
        return CalendarEntry.byDay(CalendarEntry.releases(found))
    }

    private func readReleases() async {
        let key = monthKey
        // Another month's trouble isn't this one's.
        releasesProblem = nil
        guard releases[key] == nil else { return }
        do {
            let found = try await model.ask(
                "tmdb.calendar", ["year": month.year, "month": month.month], as: TMDBReleases.self
            ).releases
            releases[key] = found
            releasesProblem = nil
            let rules = model.titleRules(language: OriginalLanguage.all)
            model.titles.look(found.map(\.item), rules, languageToo: false, model: model)
        } catch {
            releasesProblem = error.localizedDescription
        }
    }

    private func open(_ entry: CalendarEntry) {
        if let release = entry.release {
            Task { await model.openFromTMDB(release.item) }
        } else if let series = entry.series, let episode = entry.episodeId {
            model.openEpisode(episode, of: series)
        } else if let video = entry.video {
            model.playVideos([video.result], startAt: 0, channels: VideoHit.channels(of: [video]))
        }
    }
}

/// One day of the calendar: its number in the top left (in a ring of the accent colour
/// when it's today), and what's on that day, as many as there's room for, then "+2 more".
/// A day of the month before or after is left empty, a shade darker, as drawn.
private struct DayCell: View {
    let number: Int?
    let isToday: Bool
    let entries: [CalendarEntry]
    let open: (CalendarEntry) -> Void
    @State private var showsAll = false

    private static let rowHeight: CGFloat = 36
    /// The "+n more" line under the rows: smaller than a row.
    private static let moreHeight: CGFloat = 18

    var body: some View {
        Rectangle()
            .fill(number == nil ? AnyShapeStyle(.clear) : AnyShapeStyle(.primary.opacity(0.06)))
            .overlay(alignment: .topLeading) {
                if let number {
                    GeometryReader { room in
                        let fits = max(Int((room.size.height - 44) / Self.rowHeight), 0)
                        // With more than fit, as many as leave room for the "+n more" line.
                        let fitsWithMore = max(Int((room.size.height - 44 - Self.moreHeight) / Self.rowHeight), 0)
                        let shown = entries.count > fits ? fitsWithMore : entries.count
                        VStack(alignment: .leading, spacing: 4) {
                            Text("\(number)")
                                .font(.title3)
                                .monospacedDigit()
                                .foregroundStyle(isToday ? AnyShapeStyle(.white) : AnyShapeStyle(.primary))
                                .frame(minWidth: 34, minHeight: 34)
                                .background {
                                    if isToday { Circle().fill(Color.accentColor) }
                                }
                                .padding(.leading, 4)
                                .help(isToday ? "Today" : "")
                            ForEach(entries.prefix(shown)) { entry in
                                EntryRow(entry: entry) { open(entry) }
                            }
                            if shown < entries.count {
                                Button("+\(entries.count - shown) more") { showsAll = true }
                                    .buttonStyle(.plain)
                                    .font(.caption.weight(.semibold))
                                    .foregroundStyle(.secondary)
                                    .padding(.leading, 6)
                                    .popover(isPresented: $showsAll, arrowEdge: .trailing) {
                                        VStack(alignment: .leading, spacing: 4) {
                                            ForEach(entries) { entry in
                                                EntryRow(entry: entry) {
                                                    showsAll = false
                                                    open(entry)
                                                }
                                            }
                                        }
                                        .padding(10)
                                        .frame(width: 280)
                                    }
                            }
                        }
                        .padding(6)
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .clipped()
    }
}

/// An episode or a video on a day: its picture, its name and, under it, which episode or
/// whose channel.
private struct EntryRow: View {
    let entry: CalendarEntry
    let open: () -> Void

    var body: some View {
        let isVideo = entry.video != nil
        Button(action: open) {
            HStack(spacing: 7) {
                WebPicture(
                    address: WebPictures.sized(entry.picture, points: 60), symbol: isVideo ? "play.tv" : "film")
                    .frame(width: isVideo ? 42 : 21, height: 28)
                    .clipShape(RoundedRectangle(cornerRadius: 3))
                VStack(alignment: .leading, spacing: 0) {
                    Text(entry.title).font(.caption.weight(.semibold)).lineLimit(1)
                    Text(entry.detail).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer(minLength: 0)
            }
            .padding(3)
            .frame(height: 34)
            .background(.primary.opacity(0.07), in: RoundedRectangle(cornerRadius: 5))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(
            "\(entry.title) · \(entry.detail)"
                + (isVideo
                    ? ". Click to play it."
                    : entry.release != nil ? ". Click to open its page." : ". Click to open it on the series' page."))
    }
}
