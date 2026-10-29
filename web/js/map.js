// The map: OpenStreetMap tiles with the vehicle, its recent track and home on top.
//
// The tile address and attribution come from the server's settings ([map] in
// config.toml). Under the tiles lies a grid that pans and zooms with the map,
// so without tiles (no internet, or none configured) the track and the vehicle
// still read correctly. "Follow" keeps the vehicle in the middle until the map
// is dragged.

import * as L from "../vendor/leaflet/leaflet-src.esm.js";
import { formatAge } from "./fields.js";
import { Trail } from "./geo.js";

const TRAIL_POINTS = 600; // about two minutes of flight at 5 Hz
const TRAIL_STEP_M = 1; // the vehicle must move this far for a new track point
const FIRST_FIX_ZOOM = 16;
const MAX_ZOOM = 19; // until the server's settings arrive
const ZOOM_HANDLERS = ["scrollWheelZoom", "doubleClickZoom", "touchZoom"];

// Our own vehicle symbol, nose to the north. The arrow is turned to the heading;
// the label under it says why the position shown may not be current.
const VEHICLE_HTML =
  '<svg viewBox="-14 -14 28 28" width="28" height="28" aria-hidden="true">' +
  '<path d="M0 -12 L9 10 L0 5 L-9 10 Z"/></svg><span class="map-label"></span>';

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

    this.#map.on({
      dragstart: () => this.#setFollow(false), // the operator looks elsewhere
      zoomstart: () => {
        this.#zooming = true; // panning during a zoom animation upsets it
      },
      zoomend: () => {
        this.#zooming = false;
      },
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
    const element = this.#vehicle.getElement();
    if (!element) return;
    element.classList.add("is-stale");
    element.lastElementChild.textContent = "GCS offline";
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
