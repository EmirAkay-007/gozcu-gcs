// Small geometry for the map, kept free of Leaflet so it can be unit-tested in Node.

const EARTH_RADIUS_M = 6371008.8; // mean radius, as in gcs/units.py
const RAD = Math.PI / 180;

/** Great-circle distance in metres between two points in degrees. */
export function metresBetween(lat1, lon1, lat2, lon2) {
  const dLat = (lat2 - lat1) * RAD;
  const dLon = (lon2 - lon1) * RAD;
  const a =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * RAD) * Math.cos(lat2 * RAD) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
}

/** The point distanceM from (lat, lon) along bearingDeg (0 north, 90 east), on a sphere. */
export function destination(lat, lon, bearingDeg, distanceM) {
  const angle = distanceM / EARTH_RADIUS_M;
  const bearing = bearingDeg * RAD;
  const lat1 = lat * RAD;
  const lat2 = Math.asin(
    Math.sin(lat1) * Math.cos(angle) + Math.cos(lat1) * Math.sin(angle) * Math.cos(bearing),
  );
  const lon2 =
    lon * RAD +
    Math.atan2(
      Math.sin(bearing) * Math.sin(angle) * Math.cos(lat1),
      Math.cos(angle) - Math.sin(lat1) * Math.sin(lat2),
    );
  return [lat2 / RAD, lon2 / RAD];
}

/** Metres on the ground that one screen pixel covers at a Web Mercator zoom level. */
export function metresPerPixel(lat, zoom) {
  return (40075016.686 * Math.cos(lat * RAD)) / (256 * 2 ** zoom); // equator length / map width
}

/**
 * The vehicle's recent track: a new point only once it has moved minStepM,
 * so a vehicle standing still does not fill it, and at most maxPoints of them.
 */
export class Trail {
  #points = [];
  #maxPoints;
  #minStepM;

  constructor(maxPoints, minStepM) {
    this.#maxPoints = maxPoints;
    this.#minStepM = minStepM;
  }

  /** Offer a position; returns true when it became a new point of the track. */
  add(lat, lon) {
    const last = this.#points.at(-1);
    if (last && metresBetween(last[0], last[1], lat, lon) < this.#minStepM) return false;
    this.#points.push([lat, lon]);
    if (this.#points.length > this.#maxPoints) this.#points.shift();
    return true;
  }

  /** [lat, lon] pairs, oldest first. */
  get points() {
    return this.#points;
  }

  clear() {
    this.#points = [];
  }
}
