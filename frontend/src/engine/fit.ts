// Minimal Garmin FIT reader for the drafting check (positions only), and the .fit → scenario builder.
// TypeScript port of backend/vector_pacing/fit.py (read_fit) and review.py (scenario_from_fit), limited
// to what the GPS following check needs: `record` messages (global 20) with timestamp (253),
// position_lat (0), position_long (1) and gps_accuracy (31). Other fields and messages are skipped by
// size, so files from any head unit or app are accepted (normal and compressed-timestamp headers,
// developer fields, either byte order, chained files). The file CRC is verified.
// Not ported: speed/power/cadence fields and the RaceGuard power screen that used them.
import { FORMAT_ID } from './drafting';
import { haversineM } from './course';
import { EngineError } from './util';

export const MAX_FIT_BYTES = 20 * 1024 * 1024;
const FIT_EPOCH_S = 631_065_600; // 1989-12-31T00:00:00Z
const SEMICIRCLE_DEG = 180 / 2 ** 31;
const MESG_RECORD = 20;
const F_TIMESTAMP = 253;
const F_LAT = 0;
const F_LON = 1;
const F_ACCURACY = 31;
const COURSE_FROM_TRACK_SPACING_M = 5;
const MAX_RIDERS = 200;

const CRC_TABLE = [0x0000, 0xcc01, 0xd801, 0x1400, 0xf001, 0x3c00, 0x2800, 0xe401, 0xa001, 0x6c00, 0x7800, 0xb401, 0x5000, 0x9c01, 0x8801, 0x4400];

export function fitCrc(data: Uint8Array, from = 0, to = data.length): number {
  let crc = 0;
  for (let i = from; i < to; i++) {
    const byte = data[i]!;
    let tmp = CRC_TABLE[crc & 0xf]!;
    crc = ((crc >> 4) & 0x0fff) ^ tmp ^ CRC_TABLE[byte & 0xf]!;
    tmp = CRC_TABLE[crc & 0xf]!;
    crc = ((crc >> 4) & 0x0fff) ^ tmp ^ CRC_TABLE[(byte >> 4) & 0xf]!;
  }
  return crc;
}

export interface FitTrack {
  t: number[]; // Unix seconds
  lat: number[];
  lon: number[];
  accuracy: (number | null)[];
  recordsWithoutPosition: number;
}

interface Def {
  little: boolean;
  globalNum: number;
  fields: [number, number][]; // (field number, size)
  size: number;
}

/** Extract timestamped positions from a FIT file. Throws EngineError with a user-facing message. */
export function readFit(data: Uint8Array, name: string): FitTrack {
  if (data.length === 0) throw new EngineError(`${name}: the file is empty.`);
  if (data.length > MAX_FIT_BYTES) throw new EngineError(`${name}: the file is larger than 20 MB.`);
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const track: FitTrack = { t: [], lat: [], lon: [], accuracy: [], recordsWithoutPosition: 0 };
  let pos = 0;
  while (pos < data.length) {
    if (data.length - pos < 12) {
      if (pos === 0) throw new EngineError(`${name}: not a FIT file (too short).`);
      break; // trailing padding after a complete file
    }
    const headerSize = data[pos]!;
    const magic = String.fromCharCode(data[pos + 8]!, data[pos + 9]!, data[pos + 10]!, data[pos + 11]!);
    if ((headerSize !== 12 && headerSize !== 14) || magic !== '.FIT') throw new EngineError(`${name}: not a FIT file (missing .FIT header).`);
    const end = pos + headerSize + view.getUint32(pos + 4, true);
    if (end + 2 > data.length) throw new EngineError(`${name}: the FIT file is truncated.`);
    if (fitCrc(data, pos, end) !== view.getUint16(end, true)) throw new EngineError(`${name}: the FIT file is corrupted (checksum mismatch).`);
    readRecords(data, view, pos + headerSize, end, track, name);
    pos = end + 2;
  }
  return track;
}

function readRecords(data: Uint8Array, view: DataView, start: number, end: number, track: FitTrack, name: string) {
  const defs = new Map<number, Def>();
  let lastTs: number | null = null;
  const malformed = () => new EngineError(`${name}: the FIT file is malformed (a message runs past the end).`);
  let pos = start;
  while (pos < end) {
    const h = data[pos++]!;
    let ts: number | null = null;
    let local: number;
    if (h & 0x80) {
      // Compressed timestamp header.
      local = (h >> 5) & 0x03;
      const offset = h & 0x1f;
      if (lastTs !== null) {
        ts = (lastTs & ~0x1f) + offset;
        if (offset < (lastTs & 0x1f)) ts += 0x20;
        lastTs = ts;
      }
    } else if (h & 0x40) {
      // Definition message.
      local = h & 0x0f;
      if (pos + 5 > end) throw malformed();
      const little = data[pos + 1] === 0;
      const globalNum = view.getUint16(pos + 2, little);
      const n = data[pos + 4]!;
      pos += 5;
      if (pos + 3 * n > end) throw malformed();
      const fields: [number, number][] = [];
      for (let i = 0; i < n; i++) fields.push([data[pos + 3 * i]!, data[pos + 3 * i + 1]!]);
      pos += 3 * n;
      let size = fields.reduce((s, f) => s + f[1], 0);
      if (h & 0x20) {
        // Developer fields: skipped, only their size matters.
        const nd = data[pos++]!;
        if (pos + 3 * nd > end) throw malformed();
        for (let i = 0; i < nd; i++) size += data[pos + 3 * i + 1]!;
        pos += 3 * nd;
      }
      defs.set(local, { little, globalNum, fields, size });
      continue;
    } else {
      local = h & 0x0f;
    }
    const d = defs.get(local);
    if (!d) throw new EngineError(`${name}: data message before its definition; the FIT file is malformed.`);
    if (pos + d.size > end) throw malformed();
    let tsRaw: number | null = null;
    let lat: number | null = null;
    let lon: number | null = null;
    let acc: number | null = null;
    let p = pos;
    for (const [num, size] of d.fields) {
      if (num === F_TIMESTAMP && size === 4) {
        const v = view.getUint32(p, d.little);
        if (v !== 0xffffffff) tsRaw = v;
      } else if ((num === F_LAT || num === F_LON) && size === 4) {
        const v = view.getInt32(p, d.little);
        if (v !== 0x7fffffff && v !== -0x80000000) {
          if (num === F_LAT) lat = v;
          else lon = v;
        }
      } else if (num === F_ACCURACY && size === 1) {
        const v = data[p]!;
        if (v !== 0xff) acc = v;
      }
      p += size;
    }
    pos += d.size;
    if (tsRaw !== null) ts = lastTs = tsRaw;
    if (d.globalNum !== MESG_RECORD || ts === null) continue;
    if (lat === null || lon === null) {
      track.recordsWithoutPosition++;
      continue;
    }
    track.t.push(ts + FIT_EPOCH_S);
    track.lat.push(lat * SEMICIRCLE_DEG);
    track.lon.push(lon * SEMICIRCLE_DEG);
    track.accuracy.push(acc ? acc : null);
  }
}

export interface FitUpload {
  riders: { name: string; bytes: Uint8Array }[];
  /** Course line already parsed from GPX on the main thread (DOMParser is not available in workers). */
  course: { filename: string; name: string | null; points: [number, number][] } | null;
}

const stem = (fname: string) => fname.replace(/^.*[\\/]/, '').replace(/\.[^.]*$/, '');

/**
 * Build a `vector-pacing-riders/1` document from one FIT activity per rider and an optional course.
 * Without a course the first rider's track (thinned to ~5 m spacing) is used as the course line.
 */
export function scenarioFromFit(up: FitUpload): { doc: Record<string, unknown>; extraWarnings: string[][]; source: string } {
  if (up.riders.length < 2) throw new EngineError('Select at least two .fit files (one per rider): the following check compares riders.');
  if (up.riders.length > MAX_RIDERS) throw new EngineError(`At most ${MAX_RIDERS} riders are supported.`);
  const seen = new Set<string>();
  const tracks = up.riders.map(({ name, bytes }) => {
    const tr = readFit(bytes, name);
    if (tr.t.length < 2) throw new EngineError(`${name}: fewer than two GPS positions were found; was GPS recording enabled?`);
    const base = (stem(name).trim() || 'rider').slice(0, 36);
    let id = base;
    for (let n = 2; seen.has(id); n++) id = `${base}-${n}`;
    seen.add(id);
    return { id, name, tr };
  });
  let t0 = Infinity;
  for (const { tr } of tracks) for (const t of tr.t) if (t < t0) t0 = t;

  const notes: string[] = [];
  let points: [number, number][];
  let courseName: string;
  if (up.course) {
    points = up.course.points;
    courseName = (up.course.name || stem(up.course.filename)).slice(0, 120);
    notes.push(`Course line from ${up.course.filename}.`);
  } else {
    const { id, name, tr } = tracks[0]!;
    const keep = [0];
    let cum = 0;
    let bucket = 0;
    for (let i = 1; i < tr.lat.length; i++) {
      cum += haversineM(tr.lat[i - 1]!, tr.lon[i - 1]!, tr.lat[i]!, tr.lon[i]!);
      const b = Math.floor(cum / COURSE_FROM_TRACK_SPACING_M);
      if (b > bucket) keep.push(i);
      bucket = b;
    }
    if (keep[keep.length - 1] !== tr.lat.length - 1) keep.push(tr.lat.length - 1);
    points = keep.map((i) => [tr.lat[i]!, tr.lon[i]!]);
    courseName = `Track of ${id}`;
    notes.push(`No course .gpx was selected, so ${name}'s own track is used as the course line. Add the course .gpx for a neutral reference.`);
  }
  const doc = {
    format: FORMAT_ID,
    name: `${tracks.length} riders from .fit files`,
    description: notes.join(' '),
    start_time: new Date(t0 * 1000).toISOString().replace('.000Z', 'Z'),
    course: { name: courseName, points },
    riders: tracks.map(({ id, tr }) => ({
      id,
      label: id,
      samples: { t: tr.t.map((t) => t - t0), lat: tr.lat, lon: tr.lon, accuracy_m: tr.accuracy },
    })),
  };
  const extraWarnings = tracks.map(({ tr }) =>
    tr.recordsWithoutPosition ? [`${tr.recordsWithoutPosition} record(s) without a GPS position were ignored.`] : [],
  );
  return { doc, extraWarnings, source: up.riders.map((r) => r.name).join(', ').slice(0, 200) };
}
