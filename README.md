# Gözcü GCS

Browser-based ground station for **Gözcü**, a visual detect-and-avoid (DAA) system for small UAVs built on the Arduino UNO Q.
It shows live telemetry, the camera feed, a map and the Gözcü threat state, passes the operator's commands to Gözcü and to the autopilot, and records sessions for later analysis.

> **Status:** in development. Telemetry, video, the map and the Gözcü panel have been tested
> against ArduPlane SITL with Gazebo (Gözcü played by `tools/fake_gozcu.py`), the commands to
> the autopilot so far against a stand-in; nothing yet against the UNO Q board.

## Development

Requires Python 3.12; the simulator side uses ArduPilot SITL with Gazebo.

```bash
python3 -m venv .venv
.venv/bin/python -E -m pip install -e ".[dev]"
.venv/bin/python -E -m pytest                  # unit tests
.venv/bin/python -E -m ruff check .            # lint
.venv/bin/python -E tools/link_check.py        # check the MAVLink link to SITL (tcp:127.0.0.1:5762)
.venv/bin/python -E tools/fake_gozcu.py        # play Gözcü's MCU on SITL's second port (tcp:127.0.0.1:5763)
.venv/bin/python -E tools/ui_check.py          # use the page in a real browser, as an operator does
```

`-E` makes Python ignore `PYTHONPATH`. Where ROS is sourced, that variable points at ROS's
Python packages, which would otherwise leak into this project's environment.

The browser code needs no build step. With Node.js 22.7 or newer installed (on `PATH` or
through nvm), pytest also parses every script in `web/js` and runs the unit tests in
`tests/js`; without Node.js those two tests are skipped. To run only the browser tests:
`node --test tests/js/*.test.js`. The video tests run a real ffmpeg and are skipped without it.

`tools/ui_check.py` starts the station with stand-ins for the vehicle
(`tools/fake_autopilot.py`), Gözcü and the camera, opens the page in headless Firefox and
uses it as an operator would: it holds the buttons, right-clicks the map, types an altitude,
and lists what worked (in about half a minute; `--screenshots DIR` keeps a few pictures).
It needs Firefox, and ffmpeg for the video checks. Its ports are free ones, the map loads
no tiles and nothing is recorded, so it can run beside the simulator and a running station.

## Running

```bash
.venv/bin/python -E -m gcs                     # then open http://127.0.0.1:8000
```

Settings come from `config.toml` when it exists (start from a copy of `config.example.toml`);
`--conn` and `--port` override it, e.g. `--conn udpin:0.0.0.0:14551`. While the station runs,
the connection box in the top bar changes or closes the link (hold the button: a working
link's telemetry stops); it suggests the links used before, the serial ports present and
the usual SITL ports. Over a telemetry radio
plugged in by USB: `--conn /dev/ttyUSB0,57600` (its port and baud rate; on Linux the user
must be in the `dialout` group). Gözcü's reports reach the ground station on any of these
links, because the autopilot passes them on from the port the MCU is connected to. After an
update, restart the station and reload the page.

## Safety

- Every command button has to be held down for 0.6 s: a stray click or tap sends nothing.
- One command at a time goes to Gözcü, and one to the autopilot. Each is sent again at most
  twice while unanswered, and what became of it shows beside its buttons and in the event log.
- Gözcü's commands go only to Gözcü's component: the MCU decides, the station only asks.
- The station never sends an avoidance manoeuvre; that is Gözcü's job. While Gözcü avoids,
  or one of its proposals waits for an answer, the operator's commands to the autopilot are
  held.
- The autopilot checks every command as it checks any ground station's; nothing is forced.
  The RC transmitter remains the way to take over.
- Every coloured state is also written out: amber for caution, red for danger, green only
  for what is well (link up, clear of conflict).
- Only the station's own page may use it: a website open in another tab cannot send it
  commands.

## Gözcü

The right-hand panel shows what Gözcü, MAVLink component 191 of the vehicle, reports as
[docs/message_contract.md](docs/message_contract.md) defines it: the threat state, a
proposed manoeuvre and what became of it, up to three tracks, and Gözcü's health. Changes
of state, mode, switch and faults also go to the event log, and Gözcü's values and tracks
into the session recording.

The tracks are marked on the video too, with a reticle and a label, the target in its
threat colour ("Overlay" hides them in this browser); their place in the picture comes from
the camera's horizontal field of view, `[video] hfov_deg`. On the map each track is a ray
from the vehicle along its bearing, since Gözcü knows the direction of a track but not
its range.

The operator can change Gözcü's mode, approve or reject a proposal in CONFIRM mode, and
run or pause perception. Each of these buttons has to be held down for 0.6 s, so a stray
click sends nothing; a decision names the proposal it answers, so a late one is refused
rather than misapplied. These commands go only to Gözcü, never to the autopilot, one at a
time, and are sent again if unanswered; their outcome shows under the mode buttons and in
the event log. The MCU decides: the ground station only asks.

Without the board, `tools/fake_gozcu.py` plays the MCU: it connects to SITL's second serial
port as Gözcü would and follows the contract, with scenarios from a full encounter to safe
mode (`--help` lists them). The MCU's own output can be compared with it.

## Commands to the autopilot

Under the attitude indicator, the operator can set the flight mode (FBWA, CRUISE, LOITER,
RTL, AUTO, TAKEOFF, GUIDED or AUTOLAND; MANUAL is left to the RC transmitter) and arm or
disarm. A right-click on the map opens "Fly here": the vehicle switches to GUIDED and flies
to that point, at the altitude given above home (20 to 500 m; the vehicle's own altitude
is offered, but at least 50 m). The point stays marked while the vehicle goes there.
As with Gözcü, every button has to be held, one command goes at a time and is sent again
if unanswered, and the outcome shows under the buttons and in the event log.

The autopilot checks these commands as it checks any ground station's: it runs its
pre-arm checks before arming and says in its own messages why it refused, and ArduPlane
refuses a disarm from the ground while flying. Nothing is forced. The station sends none
of them while Gözcü avoids or one of its proposals waits for an answer, so nothing from
the ground works against an avoidance manoeuvre; the RC transmitter remains the way to
take over. The flight modes are sent by ArduPlane's numbers, so these commands are offered
only for ArduPlane (fixed wings and quadplanes).

## Map

The map draws OpenStreetMap tiles, so it needs internet. Without it, or with
`[map] tile_url = ""`, the vehicle, its track and home are drawn on a plain grid. For
offline use, point `tile_url` at a tile server of your own: OpenStreetMap's servers must
not be bulk-downloaded or used offline. Licenses of everything from elsewhere are listed
in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Video

The camera stream, H.264 in MPEG-TS, arrives on UDP `127.0.0.1:5600`, where Gazebo's
camera plugin sends it once its streaming has been enabled (`enable_streaming` on the
camera's topic). ffmpeg (`sudo apt install ffmpeg`) turns it into JPEG frames for the
video panel; without ffmpeg there is no picture, but recording still works. `[video]` sets
the address, a frame-rate limit for the browser (`max_fps`), or turns video off.

**REC** records the stream exactly as it arrives, without re-encoding, into the logs
folder as `<start time>_video.ts` (plays in VLC, mpv or ffplay; the first half second can
be grey until the next keyframe).

Measured with the Gazebo camera (640x480, about 19 fps, 0.9 Mbit/s) on an i7-13650HX:
ffmpeg needs about 12 % of one core for every frame (8 % with `max_fps = 10`), and a
frame reaches the browser about 100 ms after its first packet, all but 4 ms of that
because MPEG-TS completes a frame only when the next one begins. Recording costs no
measurable CPU.

## Recordings

Every run is written to a JSON Lines file named after its start time
(`2026-10-29_13-36-03.jsonl`) in a `Gözcü GCS logs` folder on the desktop, or in the folder
set as `[recording] directory`. The first line describes the session; then come all events
and, twice a second, the value of every field (`null` when unknown or stale): about 4 MB an hour.

## When something is wrong

The station says what it knows in the event log and on the panel concerned. The usual cases:

- **NO VEHICLE, "no MAVLink data on tcp:127.0.0.1:5762 yet"**: another program holds that
  port (SITL serves one client per port), or the simulation is paused. Close the other
  program, or choose another link in the connection box.
- **Video NO SIGNAL**: nothing arrives on UDP 5600; Gazebo's camera sends only once its
  streaming is enabled. **ERROR "another program is receiving it"**: another station or a
  video player has the port.
- **"cannot open /dev/ttyUSB0,57600: … Permission denied"**: add the user to the `dialout`
  group (`sudo usermod -aG dialout $USER`), then log in again.
- **A command "not sent", or "Commands held: …"**: the reason given is why; see Safety.
