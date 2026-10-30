# Gözcü ↔ ground station message contract

**Version 1 — draft, to be agreed between the MCU firmware and the ground station.**
This file is the single source of truth: both sides implement what is written here, and a
change starts here. `tools/fake_gozcu.py` produces exactly this traffic, so either side can
be tested without the other.

## Principles

- **The MCU decides.** The ground station only reports and asks. It never sends an
  avoidance manoeuvre to the autopilot; mode changes, approvals and rejections go to the
  Gözcü component, which accepts or refuses them.
- **Standard MAVLink messages only.** `NAMED_VALUE_INT`, `NAMED_VALUE_FLOAT`, `STATUSTEXT`,
  `HEARTBEAT`, `COMMAND_LONG` and `COMMAND_ACK` are in every MAVLink library and are shown by
  MAVProxy and QGroundControl as they are; no dialect has to be built. If link bandwidth
  becomes the limit (see *Bandwidth*), a later version can move to custom messages.
- **Every command is idempotent**, so a command whose acknowledgement was lost can simply be
  sent again.

## Identity and routing

| | System id | Component id |
|---|---|---|
| Gözcü (the MCU) | the vehicle's (1 in SITL) | **191** (`MAV_COMP_ID_ONBOARD_COMPUTER`) |
| Ground station | from its config (250) | from its config (190) |

The MCU talks MAVLink on one serial port of the autopilot (Serial1, D0/D1). The autopilot
routes between its links: messages without a target (all of Gözcü's reports) are forwarded
to every other link it has heard from, and a command addressed to (1, 191) is forwarded to
the link where component 191 was heard (ArduPilot `libraries/GCS_MAVLink/MAVLink_routing.cpp`,
rules 2a and 2d). Two consequences:

- Gözcü must send its `HEARTBEAT`, or the autopilot never learns where component 191 is.
- The ground station sends commands only while it hears Gözcü's heartbeat. Otherwise the
  autopilot, finding no route, would handle the command itself and refuse it.

## Gözcü → ground station

### Heartbeat, 1 Hz

`HEARTBEAT` with `type = MAV_TYPE_ONBOARD_CONTROLLER` (18), `autopilot = MAV_AUTOPILOT_INVALID`
(8), `base_mode = 0`, `custom_mode = 0`, and `system_status`:

| `system_status` | When |
|---|---|
| `MAV_STATE_ACTIVE` (4) | working normally |
| `MAV_STATE_STANDBY` (3) | the ARM/INHIBIT switch is on INHIBIT |
| `MAV_STATE_CRITICAL` (5) | safe mode: a fault in `G_FAULT` |

### Status, 2 Hz, and at once when a value marked *on change* changes

All values of one sending share the same `time_boot_ms` (the MCU's uptime).

| Name | Message | Meaning | |
|---|---|---|---|
| `G_VER` | INT | Contract version: `1` | *v1* |
| `G_STATE` | INT | `0` CLEAR, `1` ADVISORY, `2` AVOID, `3` CLEAR OF CONFLICT | on change |
| `G_MODE` | INT | `0` WARN, `1` CONFIRM, `2` AUTO | on change |
| `G_ARM` | INT | The physical switch: `0` INHIBIT, `1` ARM | on change |
| `G_PERC` | INT | Perception `0` paused, `1` running | on change, *v1* |
| `G_FAULT` | INT | Bit flags; not 0 means safe mode: `1` MPU silent for over 200 ms, `2` newest perception result older than 500 ms | on change, *v1* |
| `G_WDT` | INT | Watchdog trips since boot | *v1* |
| `G_MPU_MS` | INT | Time since the MCU last heard from the MPU, ms | *v1* |
| `G_PAGE_MS` | INT | Age of the newest perception result, ms | |
| `G_FPS` | FLOAT | Perception rate, Hz | |
| `G_LAT_MS` | FLOAT | Camera-to-decision latency of the newest result, ms | |
| `G_TEMP` | FLOAT | MPU CPU temperature, °C | |
| `G_TGT_ID` | INT | Id of the track that `G_STATE` is about; `0` none | *v1* |

### Manoeuvre proposal, 2 Hz while one exists and for 5 s after its decision; at once on change

| Name | Message | Meaning | |
|---|---|---|---|
| `G_PROP` | INT | Proposal id, counting up from 1; `0` no proposal | *v1* |
| `G_MNVR` | INT | `0` none, `1` climb, `2` descend, `3` turn left, `4` turn right | *v1* |
| `G_DEC` | INT | `0` waiting for the operator, `1` approved (executing), `2` refused by the MCU, `3` rejected by the operator, `4` expired | *v1* |
| `G_WHY` | INT | Why the MCU refused it: `0` —, `1` outside the flight envelope, `2` below minimum altitude, `3` speed limit, `4` switch on INHIBIT | *v1* |
| `G_TTL_MS` | INT | Time left for the operator to decide, ms | *v1* |

In WARN mode there are no proposals. In CONFIRM mode a proposal waits for the operator until
`G_TTL_MS` runs out. In AUTO mode the MCU decides at once (`G_DEC` goes straight to `1` or `2`).

### Tracks, up to 4 Hz while there are tracks, 1 Hz while there are none

One *track frame* holds up to 3 tracks, the most threatening first, as `T1_…`, `T2_…`, `T3_…`.
Every value of a frame carries the frame's `time_boot_ms`, and **`G_NTRK` comes last**: it
closes the frame. The ground station shows the tracks of a frame only once it has its
`G_NTRK`, so a frame is never mixed with the one before, and a track not in the latest frame
disappears.

| Name (k = 1…3) | Message | Meaning | |
|---|---|---|---|
| `Tk_ID` | INT | Track id | *v1* |
| `Tk_CLS` | INT | `0` unknown, `1` aircraft, `2` drone, `3` bird | |
| `Tk_CONF` | FLOAT | Class confidence, 0…1 | |
| `Tk_AZ` | FLOAT | Azimuth in the camera image, degrees, + right of the optical axis | |
| `Tk_EL` | FLOAT | Elevation in the camera image, degrees, + above the optical axis | |
| `Tk_RATE` | FLOAT | Bearing rate, deg/s, after removing the vehicle's own rotation | |
| `Tk_PX` | FLOAT | Apparent size: the longer side of the box, pixels | |
| `Tk_TAU` | FLOAT | Time to collision, s; `-1` when not closing | |
| `G_NTRK` | INT | Number of tracks in this frame (0…3); sent last | *v1* |

`Tk_AZ` and `Tk_EL` are as seen by the camera, from the box centre and the camera's
intrinsics: `AZ = atan((u − cx) / fx)`, `EL = atan((cy − v) / fy)`. They are deliberately
*not* compensated for the vehicle's rotation, so the ground station can mark the track on the
video. Only `Tk_RATE` is compensated, as threat assessment needs it.

### Event texts

`STATUSTEXT` from (1, 191), one line each (at most 50 characters, or sent in chunks), for the
operator's event log; the ground station labels them as Gözcü's. The panel itself works from
the values above, never from these texts. For example:

| Severity | Text |
|---|---|
| WARNING | `ADVISORY: track 3 drone, tau 9.1 s` |
| WARNING | `Proposal 7: climb, approve within 5 s` |
| CRITICAL | `AVOID: climbing (proposal 7)` |
| WARNING | `Proposal 7 refused: below minimum altitude` |
| CRITICAL | `Safe mode: MPU silent 230 ms` |
| NOTICE | `Clear of conflict` |

## Ground station → Gözcü

### Commands

`COMMAND_LONG` to `target_system` = the vehicle's, `target_component = 191`, with
`command = MAV_CMD_USER_1` (31010). `confirmation` is 0, and goes up by one each time the
same command is sent again. Unused parameters are 0.

| Action | `param1` | `param2` | `param3` |
|---|---|---|---|
| Set mode | `0` | `0` WARN, `1` CONFIRM, `2` AUTO | — |
| Decide on a proposal | `1` | `1` approve, `0` reject | the proposal id (`G_PROP`) *v1* |
| Perception | `2` | `1` run, `0` pause *v1* | — |

The proposal id makes a late click harmless: the MCU refuses a decision about any proposal
other than the one waiting.

### Acknowledgement

Gözcü answers every command with `COMMAND_ACK` from (1, 191): `command = 31010`,
`target_system`/`target_component` = the sender's, and `result`:

| `result` | Meaning |
|---|---|
| `MAV_RESULT_ACCEPTED` (0) | Done; the status reports the new state |
| `MAV_RESULT_TEMPORARILY_REJECTED` (1) | Busy; may be sent again later |
| `MAV_RESULT_DENIED` (2) | Refused; `result_param2` says why |
| `MAV_RESULT_UNSUPPORTED` (3) | Unknown action (`param1`) |

`result_param2` on DENIED: `1` switch on INHIBIT, `2` no such proposal waiting (expired or
already decided), `3` not in CONFIRM mode, `4` value out of range, `5` in safe mode.

The ground station keeps one command in flight. Without an answer within 1 s it sends the
same command again, at most 3 times in all, and then reports "no answer from Gözcü". A mode
choice shows as *pending* until it is acknowledged and `G_MODE` reports it.

## Example: a CONFIRM-mode encounter

1. `G_STATE = 1`, `G_TGT_ID = 3`; text `ADVISORY: track 3 drone, tau 9.1 s`.
2. `G_PROP = 7`, `G_MNVR = 1`, `G_DEC = 0`, `G_TTL_MS = 5000`; text `Proposal 7: climb, approve within 5 s`.
3. The operator approves: `COMMAND_LONG(31010, param1 = 1, param2 = 1, param3 = 7)`, answered `ACCEPTED`.
4. The MCU checks the flight envelope and executes: `G_DEC = 1`, `G_STATE = 2`; text `AVOID: climbing (proposal 7)`.
   Had the envelope check failed: `G_DEC = 2`, `G_WHY = 2`; text `Proposal 7 refused: below minimum altitude`.
5. The threat passes: `G_STATE = 3`, then `0`; `G_PROP` returns to `0` 5 s after the decision.

## Timing on the ground station

| Data | Shown as stale after |
|---|---|
| Gözcü's heartbeat | 3 s: *Gözcü silent*, commands disabled |
| Status values | 1.5 s |
| Track frame | 1 s |

## Bandwidth

Measured as pymavlink packs these messages (MAVLink 2 trims the unused end of each name):
the status values 345 bytes, a proposal 130 bytes, one track 206 bytes, `G_NTRK` 26 bytes.

| Load | Bytes/s | Share of a 57600-baud link (5760 B/s) |
|---|---|---|
| Status + one track at 4 Hz | 1640 | 28 % |
| Status + proposal + three tracks at 4 Hz | 3550 | 62 % |

The autopilot sends these on to the ground station alongside its own telemetry (about
1 kB/s at the rates this ground station asks for). Hence: run the MCU ↔ autopilot link at
**115200 baud or more**, and over a 57600-baud telemetry radio send tracks at 2 Hz. A custom
message carrying one whole track would be about 40 bytes instead of 206.

## Testing without the board

`tools/fake_gozcu.py` connects to the autopilot like the MCU does: to SITL's second serial
port, `tcp:127.0.0.1:5763`, as (1, 191). ArduPilot routes its traffic to the ground station
on `tcp:127.0.0.1:5762` and to MAVProxy, and routes the commands back, exactly as it will
for the real MCU. Scenarios: clear sky, advisory, avoid, a CONFIRM proposal, MPU silent,
perception stale. For hardware-in-the-loop tests, mavlink-router takes SITL's place as the
hub between SITL, the ground station and the MCU's serial adapter.

## Changes from the first draft

The first draft (project brief) had `G_STATE`, `G_MODE`, `G_ARM`, `G_FPS`, `G_LAT_MS`,
`G_PAGE_MS`, `G_TEMP`, the `Tk_` fields except `Tk_ID`, the two commands, and proposals and
refusal reasons as text only. Version 1 adds the values marked *v1*, for what the panel has
to show: the contract version, the watchdog (faults, trips, MPU silence), perception
running or paused, which track the state is about, a track id, an end marker for track
frames, and the proposal as values with an id that the operator's decision refers to.
