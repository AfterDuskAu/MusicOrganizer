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

    var body: some View {
        let uploads = CalendarEntry.byDay(CalendarEntry.uploads(model.calendarUploads))
        let today = CalendarEntry.day(of: Date())
        VStack(spacing: 0) {
            header
            if model.ownSeries.isEmpty, model.followedChannels.isEmpty {
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
                                entries: day.map { (episodes[$0] ?? []) + (uploads[$0] ?? []) } ?? [],
                                open: open)
                        }
                    }
                }
            }
            .frame(maxHeight: .infinity)
        }
        .padding(.horizontal, 12)
        .padding(.bottom, 12)
        .onChange(of: model.seriesRead, initial: true) { layOut() }
        .task { await model.readSeries(model.ownSeries) }
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
            if model.readingSeries || model.readingUploads {
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

    private func layOut() {
        let all = model.ownSeries.flatMap { series in
            CalendarEntry.episodes(of: series, model.seriesEpisodes[series.id] ?? [])
        }
        episodes = CalendarEntry.byDay(all)
    }

    private func open(_ entry: CalendarEntry) {
        if let series = entry.series, let episode = entry.episodeId {
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

    var body: some View {
        Rectangle()
            .fill(number == nil ? AnyShapeStyle(.clear) : AnyShapeStyle(.primary.opacity(0.06)))
            .overlay(alignment: .topLeading) {
                if let number {
                    GeometryReader { room in
                        let fits = max(Int((room.size.height - 44) / Self.rowHeight), 0)
                        let shown = entries.count > fits ? max(fits - 1, 0) : entries.count
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
                + (isVideo ? ". Click to play it." : ". Click to open it on the series' page."))
    }
}
