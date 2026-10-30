// The map: OpenStreetMap tiles with the vehicle, its recent track and home on top.
//
// The tile address and attribution come from the server's settings ([map] in
// config.toml). Under the tiles lies a grid that pans and zooms with the map,
// so without tiles (no internet, or none configured) the track and the vehicle
// still read correctly. "Follow" keeps the vehicle in the middle until the map
// is dragged.
//
// Gözcü's tracks have a bearing but no range, so each is drawn as a ray from the
// vehicle along that bearing, of the same length on the screen at every zoom.
//
// A right-click picks a point to fly to (flyto.js), which is marked until the
// vehicle no longer goes there.

import * as L from "../vendor/leaflet/leaflet-src.esm.js";
import { formatAge } from "./fields.js";
import { Trail, destination, metresPerPixel } from "./geo.js";
import { trackLabel, trackMarks } from "./gozcu.js";

const TRAIL_POINTS = 600; // about two minutes of flight at 5 Hz
const TRAIL_STEP_M = 1; // the vehicle must move this far for a new track point
const FIRST_FIX_ZOOM = 16;
const MAX_ZOOM = 19; // until the server's settings arrive
const ZOOM_HANDLERS = ["scrollWheelZoom", "doubleClickZoom", "touchZoom"];
const RAY_PX = 140; // length of a track's ray on the screen...
const RAY_SHARE = 0.35; // ...but no more than this share of a small map, so its end shows
const MAX_RAYS = 3; // the contract reports at most three tracks

// Our own vehicle symbol, nose to the north. The arrow is turned to the heading;
// the label under it says why the position shown may not be current.
const VEHICLE_HTML =
  '<svg viewBox="-14 -14 28 28" width="28" height="28" aria-hidden="true">' +
  '<path d="M0 -12 L9 10 L0 5 L-9 10 Z"/></svg><span class="map-label"></span>';

// A ring with a cross for a point to fly to, drawn twice: dark under light, as the track.
const TARGET_SHAPE = '<circle r="6"/><path d="M0 -11V-3M0 3V11M-11 0H-3M3 0H11"/>';
const TARGET_HTML =
  '<svg viewBox="-12 -12 24 24" width="24" height="24" aria-hidden="true">' +
  `<g class="casing">${TARGET_SHAPE}</g><g class="line">${TARGET_SHAPE}</g></svg>` +
  '<span class="map-label"></span>';

// Empty 256-pixel tiles with an outline (see .map-grid-tile in app.css).
const GridLayer = L.GridLayer.extend({
  createTile() {
    const tile = document.createElement("div");
    tile.className = "map-grid-tile";
    return tile;
  },
});

const isAt = (position, lat, lon) => position !== null && position[0] === lat && position[1] === lon;

/** Why the vehicle on the map may not be where it is now; "" while its position is current. */
function positionProblem(lat, lon) {
  if (lat?.value == null || lon?.value == null) return lat?.reason ?? lon?.reason ?? "no position";
  if (lat.stale || lon.stale) return `${formatAge(lat.age)} old`;
  return "";
}

export class MapPanel {
  #map;
  #settings = null;
  #tiles = null;
  #vehicle;
  #home;
  #trailLines;
  #trail = new Trail(TRAIL_POINTS, TRAIL_STEP_M);
  #position = null; // [lat, lon] of the vehicle as shown
  #homePosition = null;
  #direction = null;
  #centred = false;
  #follow = true;
  #zooming = false;
  #followButton;
  #notice;
  #rays;
  #rayLabel;
  #marks = null; // Gözcü's fresh tracks: {tracks, target, level}
  #drawnRays = ""; // what the rays show now, so an unchanged picture is not redrawn
  #target; // the point to fly to
  #drawnTarget = "null"; // what it shows now, as for the rays

  constructor(container, followButton, notice) {
    this.#followButton = followButton;
    this.#notice = notice;
    this.#map = new L.Map(container, { maxZoom: MAX_ZOOM, fadeAnimation: false });
    this.#map.setView([0, 0], 2);

    const attribution = this.#map.attributionControl;
    attribution.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
    // Its links open in a new tab, so a stray click never takes the station's page away.
    attribution.getContainer().addEventListener("click", (event) => {
      const link = event.target.closest("a[href]");
      if (!link) return;
      event.preventDefault();
      window.open(link.href, "_blank", "noopener");
    });

    new GridLayer({ zIndex: 0 }).addTo(this.#map);
    new L.Control.Scale({ imperial: false }).addTo(this.#map);
    // A dark casing under the line keeps the track visible on light tiles and on the grid.
    this.#trailLines = [
      ["map-trail-casing", 6],
      ["map-trail", 3],
    ].map(([className, weight]) =>
      new L.Polyline([], { className, weight, interactive: false }).addTo(this.#map),
    );
    // Added to the map once their position is known.
    this.#vehicle = new L.Marker([0, 0], {
      icon: new L.DivIcon({ className: "map-vehicle", html: VEHICLE_HTML, iconSize: [28, 28] }),
      interactive: false,
      keyboard: false,
      zIndexOffset: 1000, // above home
    });
    this.#home = new L.Marker([0, 0], {
      icon: new L.DivIcon({ className: "map-home", html: "H", iconSize: [18, 18] }),
      interactive: false,
      keyboard: false,
    });
    this.#rays = Array.from(
      { length: MAX_RAYS },
      () => new L.Polyline([], { className: "map-ray", weight: 2, interactive: false }),
    );
    this.#rayLabel = new L.Marker([0, 0], {
      icon: new L.DivIcon({ className: "map-ray-label", html: "", iconSize: null }),
      interactive: false,
      keyboard: false,
    });
    this.#target = new L.Marker([0, 0], {
      icon: new L.DivIcon({ className: "map-target", html: TARGET_HTML, iconSize: [24, 24] }),
      interactive: false,
      keyboard: false,
      zIndexOffset: 500, // under the vehicle, above home
    });

    this.#map.on({
      dragstart: () => this.#setFollow(false), // the operator looks elsewhere
      zoomstart: () => {
        this.#zooming = true; // panning during a zoom animation upsets it
      },
      zoomend: () => {
        this.#zooming = false;
        this.#drawRays(); // a ray keeps its length on the screen
      },
      resize: () => this.#drawRays(), // swapped, enlarged: rays to suit the new size
    });
    followButton.addEventListener("click", () => this.#setFollow(!this.#follow));
    this.#setFollow(true);
    // Leaflet must hear when its box changes size: window, enlarge, log folded.
    new ResizeObserver(() => this.#map.invalidateSize()).observe(container);
  }

  /** Tile settings from the server's hello message, which comes again on every reconnect. */
  configure(settings) {
    const { tile_url: tileUrl, attribution, max_zoom: maxZoom } = settings;
    const key = JSON.stringify(settings);
    if (key === this.#settings) return;
    this.#settings = key;
    this.#map.setMaxZoom(maxZoom);
    this.#tiles?.remove();
    this.#tiles = null;
    if (!tileUrl) {
      this.#showNotice("No map tiles set: grid only");
      return;
    }
    this.#notice.hidden = true;
    this.#tiles = new L.TileLayer(tileUrl, { attribution, maxZoom })
      .on("tileerror", () => this.#showNotice("Map tiles unavailable (offline?): grid only"))
      .on("tileload", () => {
        this.#notice.hidden = true;
      })
      .addTo(this.#map);
  }

  update(fields) {
    const { lat, lon } = fields;
    if (lat?.value != null && lon?.value != null) this.#moveVehicle(lat.value, lon.value);
    const element = this.#vehicle.getElement(); // none before the first position
    if (element) {
      const problem = positionProblem(lat, lon);
      element.classList.toggle("is-stale", problem !== "");
      element.lastElementChild.textContent = problem;
      this.#turnVehicle(element, fields);
    }
    this.#showHome(fields.home_lat, fields.home_lon);
  }

  /** The ground station is unreachable: the position shown may be old. */
  showOffline() {
    this.#marks = null;
    this.#drawRays();
    const element = this.#vehicle.getElement();
    if (!element) return;
    element.classList.add("is-stale");
    element.lastElementChild.textContent = "GCS offline";
  }

  /** Gözcü's part of a snapshot: a ray for each of its fresh tracks. */
  showTracks(gozcu) {
    this.#marks = trackMarks(gozcu);
    this.#drawRays();
  }

  /** Call handler({lat, lon}) when the operator right-clicks a point of the map. */
  onPick(handler) {
    // With a listener, Leaflet keeps the browser's own menu closed.
    this.#map.on("contextmenu", ({ latlng }) => handler({ lat: latlng.lat, lon: latlng.lng }));
  }

  /** Mark a point to fly to, {lat, lon, text, sent}, or none (null). */
  showTarget(target) {
    const drawn = JSON.stringify(target);
    if (drawn === this.#drawnTarget) return; // snapshots come ten times a second
    this.#drawnTarget = drawn;
    if (target === null) {
      this.#target.remove();
      return;
    }
    this.#target.setLatLng([target.lat, target.lon]);
    if (!this.#map.hasLayer(this.#target)) this.#target.addTo(this.#map);
    const element = this.#target.getElement();
    element.classList.toggle("is-sent", target.sent); // dashed until sent
    element.lastElementChild.textContent = target.text;
  }

  #drawRays() {
    const marks = this.#marks;
    const position = this.#position;
    const usable = marks !== null && position !== null && this.#direction !== null;
    const tracks = usable ? marks.tracks.slice(0, MAX_RAYS) : [];
    // Snapshots come ten times a second; redraw only what changed.
    const zoom = this.#map.getZoom();
    const size = this.#map.getSize();
    const pixels = Math.min(RAY_PX, RAY_SHARE * Math.min(size.x, size.y));
    const drawn = JSON.stringify([tracks, marks?.target, marks?.level, position, this.#direction, zoom, pixels]);
    if (drawn === this.#drawnRays) return;
    this.#drawnRays = drawn;

    let label = null;
    this.#rays.forEach((ray, i) => {
      const track = tracks[i];
      if (track === undefined || track.az === null) {
        ray.remove();
        return;
      }
      const length = pixels * metresPerPixel(position[0], zoom);
      const end = destination(...position, this.#direction + track.az, length);
      ray.setLatLngs([position, end]);
      if (!this.#map.hasLayer(ray)) ray.addTo(this.#map);
      const isTarget = marks.target !== null && track.id === marks.target;
      const path = ray.getElement();
      path.classList.toggle("is-target", isTarget);
      path.dataset.level = isTarget ? marks.level : "";
      if (isTarget) label = { end, text: trackLabel(track, true), level: marks.level };
    });
    if (label === null) {
      this.#rayLabel.remove();
      return;
    }
    this.#rayLabel.setLatLng(label.end);
    if (!this.#map.hasLayer(this.#rayLabel)) this.#rayLabel.addTo(this.#map);
    const element = this.#rayLabel.getElement();
    element.textContent = label.text; // words with the colour, never colour alone
    element.dataset.level = label.level;
  }

  #moveVehicle(lat, lon) {
    if (isAt(this.#position, lat, lon)) return; // snapshots come faster than positions
    this.#position = [lat, lon];
    if (!this.#map.hasLayer(this.#vehicle)) this.#vehicle.addTo(this.#map);
    this.#vehicle.setLatLng(this.#position);
    if (this.#trail.add(lat, lon)) {
      for (const line of this.#trailLines) line.setLatLngs(this.#trail.points);
    }
    if (!this.#centred) {
      this.#map.setView(this.#position, FIRST_FIX_ZOOM);
      this.#centred = true;
    } else if (this.#follow && !this.#zooming) {
      this.#map.panTo(this.#position, { animate: false });
    }
  }

  #turnVehicle(element, { yaw, heading }) {
    // ATTITUDE's yaw comes ten times a second, VFR_HUD's heading five: yaw while fresh.
    const direction = yaw?.value != null && !yaw.stale ? yaw.value : heading?.value;
    if (direction == null || direction === this.#direction) return;
    this.#direction = direction;
    element.firstElementChild.style.transform = `rotate(${direction}deg)`;
    this.#drawRays(); // the rays turn with the vehicle, as the camera does
  }

  #showHome(lat, lon) {
    if (lat?.value == null || lon?.value == null) return;
    if (isAt(this.#homePosition, lat.value, lon.value)) return;
    this.#homePosition = [lat.value, lon.value];
    if (!this.#map.hasLayer(this.#home)) this.#home.addTo(this.#map);
    this.#home.setLatLng(this.#homePosition);
  }

  #setFollow(on) {
    this.#follow = on;
    this.#followButton.setAttribute("aria-pressed", String(on));
    // While following, zooming keeps the vehicle in the middle instead of the pointer.
    for (const name of ZOOM_HANDLERS) {
      if (this.#map.options[name]) this.#map.options[name] = on ? "center" : true;
    }
    if (on && this.#position) this.#map.panTo(this.#position, { animate: false });
  }

  #showNotice(text) {
    this.#notice.textContent = text;
    this.#notice.hidden = false;
  }
}
