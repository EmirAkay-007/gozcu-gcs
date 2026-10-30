"""Checks the station's page in a real browser, used the way an operator uses it.

The station runs with stand-ins for everything it talks to: the vehicle
(fake_autopilot.py), Gözcü's MCU (fake_gozcu.py, in CONFIRM mode, its traffic
routed through the vehicle as ArduPilot routes it) and, when ffmpeg can make one,
a plain grey camera stream. Headless Firefox opens the page through WebDriver
BiDi; the check holds buttons, right-clicks the map and types as an operator
would, and says what worked. Every port is a free one, the map loads no tiles and
nothing is recorded, so it can run beside the simulator and the operator's own
station.

    .venv/bin/python -E tools/ui_check.py [--screenshots DIR]

It takes about a minute, and exits with 1 when a check fails.
"""

import argparse
import base64
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import ExitStack, suppress
from pathlib import Path
from typing import Any

from fake_autopilot import FakeAutopilot
from fake_gozcu import CONFIRM, FakeGozcu
from websockets.sync.client import connect

from gcs.mav import mavlink

ROOT = Path(__file__).resolve().parent.parent
POSITION = (-35.363261, 149.165230, 120.0)  # ArduPilot's SITL home, 120 m above it
HOLD_MS = 750  # a command button needs 600
MARKED_PX = 50  # coloured pixels that make a mark on the picture
ESCAPE = "\ue00c"  # the Escape key, as WebDriver names it

# What the page shows, as plain data
STATE = """(() => {
  const byId = (id) => document.getElementById(id);
  const text = (id) => byId(id).textContent;
  const can = (id) => !byId(id).disabled;
  const value = (selector) => document.querySelector(`${selector} .v`)?.textContent ?? null;
  const mark = document.querySelector(".map-target");
  const label = document.querySelector(".map-ray-label");
  const gozcuMode = document.querySelector("[data-mode][aria-pressed='true']");
  const rows = [...document.querySelectorAll("#tracks tr")];
  const rays = [...document.querySelectorAll("path.map-ray")];
  const ray = (path) =>
    path.classList.contains("is-target") ? `target ${path.dataset.level}` : "track";
  return JSON.stringify({
    link: text("link-state"),
    vehicle: text("vehicle"),
    mode: value("#topbar [data-key='mode']"),
    armed: value("#armed"),
    control: {
      set: can("control-set"),
      arm: can("control-arm"),
      disarm: can("control-disarm"),
      line: text("control-line"),
    },
    fly: {
      open: !byId("fly-here").hidden,
      place: text("fly-here-place"),
      alt: byId("fly-here-alt").value,
      go: can("fly-here-go"),
      line: text("fly-here-line"),
      focused: document.activeElement === byId("fly-here-alt"),
    },
    mark: mark ? { sent: mark.classList.contains("is-sent"), label: mark.textContent } : null,
    threat: text("threat"),
    proposal: text("proposal"),
    deciding: !byId("decision").hidden,
    gozcuLine: text("command-line"),
    gozcuMode: gozcuMode ? gozcuMode.textContent : null,
    tracks: rows.filter((row) => !row.querySelector(".empty")).length,
    rays: rays.map(ray),
    rayLabel: label ? label.textContent : null,
    video: text("video-info"),
  });
})()"""

# Pixels of the picture in the marks' colours: on a grey picture, only marks have them
MARKS = """(() => {
  const canvas = document.getElementById("video-canvas");
  const pixels = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
  const style = getComputedStyle(document.documentElement);
  const rgb = (name) => {
    const hex = style.getPropertyValue(name).trim();
    return [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  };
  const counts = { accent: 0, caution: 0, alarm: 0 };
  const colours = Object.keys(counts).map((name) => [name, rgb(`--${name}`)]);
  for (let i = 0; i < pixels.length; i += 4) {
    const near = (colour) => colour.every((value, k) => Math.abs(pixels[i + k] - value) < 40);
    const found = colours.find(([, colour]) => near(colour));
    if (found) counts[found[0]] += 1;
  }
  return JSON.stringify(counts);
})()"""


def free_port(kind: int = socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Report:
    """Says how each check went as it goes."""

    def __init__(self) -> None:
        self.failed = 0
        self.passed = 0
        self.skipped = 0

    def check(self, name: str, ok: bool, detail: Any = "") -> None:
        if ok:
            self.passed += 1
            print(f"PASS  {name}", flush=True)
        else:
            self.failed += 1
            print(f"FAIL  {name}: {detail}", flush=True)

    def skip(self, name: str, why: str) -> None:
        self.skipped += 1
        print(f"SKIP  {name}: {why}", flush=True)


class Browser:
    """Headless Firefox, driven through WebDriver BiDi."""

    def __init__(self, program: str) -> None:
        snap = Path.home() / "snap/firefox/common"  # a snap's Firefox can only use its own folder
        where = snap if snap.is_dir() else None
        self._profile = tempfile.mkdtemp(prefix="gcs-ui-check-", dir=where)
        self._port = free_port()
        command = [program, "--headless", "--no-remote", "--profile", self._profile]
        command += ["--window-size=1440,900", f"--remote-debugging-port={self._port}"]
        self._process = subprocess.Popen(
            command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self._connection = ExitStack()  # the WebSocket to Firefox, once open
        self._ws: Any = None
        self._next_id = 0
        self._context = ""
        self.log: list[dict[str, Any]] = []  # console messages and JavaScript errors

    def open(self, url: str, timeout_s: float = 30.0) -> None:
        deadline = time.monotonic() + timeout_s
        while self._ws is None:
            try:
                websocket = connect(f"ws://127.0.0.1:{self._port}/session", max_size=None)
            except OSError:  # Firefox is still starting
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.3)
                continue
            self._ws = self._connection.enter_context(websocket)
        self.call("session.new", capabilities={})
        self.call("session.subscribe", events=["log.entryAdded"])
        self._context = self.call("browsingContext.getTree")["contexts"][0]["context"]
        self.call("browsingContext.navigate", context=self._context, url=url, wait="complete")

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        self._next_id += 1
        self._ws.send(json.dumps({"id": self._next_id, "method": method, "params": params}))
        while True:
            reply = json.loads(self._ws.recv())
            if reply.get("method") == "log.entryAdded":
                self.log.append(reply["params"])
            if reply.get("id") == self._next_id:
                if reply.get("type") == "error":
                    raise RuntimeError(f"{method}: {reply.get('error')}: {reply.get('message')}")
                return reply["result"]

    def evaluate(self, expression: str) -> Any:
        reply = self.call(
            "script.evaluate",
            expression=expression,
            target={"context": self._context},
            awaitPromise=False,
        )
        if reply.get("type") != "success":
            raise RuntimeError(f"the page could not run a script: {reply}")
        return reply["result"].get("value")

    def press(self, selector: str, hold_ms: int = 0, button: int = 0, dx: int = 0, dy: int = 0):
        """A real mouse press dx, dy from the middle of the element, held for hold_ms;
        button 2 is the right one."""
        element = f"document.querySelector({json.dumps(selector)})"
        box = json.loads(self.evaluate(f"JSON.stringify({element}.getBoundingClientRect())"))
        x = round(box["x"] + box["width"] / 2 + dx)
        y = round(box["y"] + box["height"] / 2 + dy)
        actions = [
            {"type": "pointerMove", "x": x, "y": y},
            {"type": "pointerDown", "button": button},
            {"type": "pause", "duration": hold_ms},
            {"type": "pointerUp", "button": button},
        ]
        mouse = {"type": "pointer", "id": "mouse", "parameters": {"pointerType": "mouse"}}
        self._act({**mouse, "actions": actions})

    def keys(self, text: str) -> None:
        """Real key presses, to whatever has the focus."""
        actions = []
        for key in text:
            actions += [{"type": "keyDown", "value": key}, {"type": "keyUp", "value": key}]
        self._act({"type": "key", "id": "keyboard", "actions": actions})

    def _act(self, source: dict[str, Any]) -> None:
        self.call("input.performActions", context=self._context, actions=[source])

    def screenshot(self, path: Path) -> None:
        shot = self.call("browsingContext.captureScreenshot", context=self._context)
        path.write_bytes(base64.b64decode(shot["data"]))

    def close(self) -> None:
        if self._ws is not None:
            with suppress(OSError, RuntimeError):  # it is going anyway
                self.call("session.end")
        self._connection.close()
        stop(self._process)
        shutil.rmtree(self._profile, ignore_errors=True)


class Station:
    """The ground station, as python -m gcs, with settings of its own in folder."""

    def __init__(self, folder: Path, vehicle: str, video_port: int | None) -> None:
        video = f"port = {video_port}" if video_port else "enabled = false"
        config = folder / "config.toml"
        config.write_text(
            f'[recording]\nenabled = false\n[map]\ntile_url = ""\n[video]\n{video}\n',
            encoding="utf-8",
        )
        port = free_port()
        self.url = f"http://127.0.0.1:{port}/"
        self.log_path = folder / "station.log"
        self._log = self.log_path.open("w", encoding="utf-8")
        command = [sys.executable, "-E", "-m", "gcs", "--config", str(config)]
        command += ["--conn", vehicle, "--port", str(port)]
        self._process = subprocess.Popen(
            command, cwd=ROOT, stdout=self._log, stderr=subprocess.STDOUT
        )

    def close(self) -> str:
        """Stop it, and return what it logged."""
        stop(self._process)
        self._log.close()
        return self.log_path.read_text(encoding="utf-8")


def start_camera(port: int) -> subprocess.Popen | None:
    """A plain grey H.264 stream in MPEG-TS to the station, as the camera sends one; None
    without an ffmpeg that can make it. Grey, so that only the marks are in colour."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    encoders = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True)
    if "libx264" not in encoders.stdout:
        return None
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-re"]
    command += ["-f", "lavfi", "-i", "color=c=gray:s=640x480:r=20"]
    # Encoded as the camera does: a keyframe every half second, no B-frames.
    command += ["-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency"]
    command += ["-g", "10", "-bf", "0", "-pix_fmt", "yuv420p"]
    command += ["-f", "mpegts", f"udp://127.0.0.1:{port}?pkt_size=1316"]
    return subprocess.Popen(command, stdin=subprocess.DEVNULL)


def stop(process: subprocess.Popen) -> None:
    process.terminate()
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def page(browser: Browser) -> dict[str, Any]:
    return json.loads(browser.evaluate(STATE))


def wait(browser: Browser, condition: Callable[[dict[str, Any]], bool], timeout_s: float) -> dict:
    """What the page shows once condition holds for it, or when timeout_s is up."""
    deadline = time.monotonic() + timeout_s
    while True:
        state = page(browser)
        if condition(state) or time.monotonic() >= deadline:
            return state
        time.sleep(0.2)


def marks(browser: Browser) -> dict[str, int]:
    return json.loads(browser.evaluate(MARKS))


def choose_mode(browser: Browser, mode: str) -> None:
    browser.evaluate(
        "(() => { const select = document.getElementById('control-mode');"
        f" select.value = {json.dumps(mode)}; select.dispatchEvent(new Event('change')); }})()"
    )


# The checks, in the order an operator meets them


def check_start(browser: Browser, report: Report, camera: bool) -> None:
    state = wait(browser, lambda s: s["link"] == "CONNECTED" and s["mode"] == "FBWA", 20)
    report.check(
        "the page opens and the station links to the vehicle",
        state["link"] == "CONNECTED" and state["vehicle"] == "FIXED_WING 1/1",
        f"link {state['link']}, vehicle {state['vehicle']}",
    )
    if not camera:
        report.skip("the camera picture arrives", "no ffmpeg with libx264 to make a stream")
        return
    state = wait(browser, lambda s: "fps" in s["video"], 15)
    report.check("the camera picture arrives", "fps" in state["video"], "no picture")


def check_vehicle(browser: Browser, report: Report, vehicle: FakeAutopilot, shots: Path | None):
    wait(browser, lambda s: s["control"]["arm"], 5)
    browser.press("#control-arm")  # a click, not a hold
    time.sleep(1.0)
    state = page(browser)
    report.check(
        "a click on a command button sends nothing",
        not vehicle.vehicle_commands and state["armed"] == "DISARMED",
        f"the vehicle got {len(vehicle.vehicle_commands)} command(s)",
    )

    choose_mode(browser, "LOITER")
    browser.press("#control-set", HOLD_MS)
    state = wait(browser, lambda s: s["mode"] == "LOITER" and "accepted" in s["control"]["line"], 5)
    report.check(
        "holding Set mode changes the flight mode",
        state["mode"] == "LOITER" and state["control"]["line"] == "mode LOITER: accepted",
        f"mode {state['mode']}, {state['control']['line']!r}",
    )

    browser.press("#control-arm", HOLD_MS)
    state = wait(browser, lambda s: s["armed"] == "ARMED" and s["control"]["disarm"], 5)
    control = state["control"]
    report.check(
        "holding ARM arms, and then only DISARM can be pressed",
        state["armed"] == "ARMED" and not control["arm"] and control["disarm"],
        f"{state['armed']}, {control}",
    )

    browser.press("#map", button=2, dx=60, dy=-40)
    state = wait(browser, lambda s: s["fly"]["open"], 3)
    fly = state["fly"]
    report.check(
        "a right-click on the map opens Fly here, offering the vehicle's altitude",
        fly["open"]
        and fly["alt"] == "120"
        and "from the vehicle" in fly["place"]
        and fly["focused"]
        and state["mark"] == {"sent": False, "label": ""},
        f"{fly}, mark {state['mark']}",
    )
    if shots:
        browser.screenshot(shots / "fly_here.png")

    browser.keys("15")  # in place of the altitude offered
    state = wait(browser, lambda s: s["fly"]["alt"] == "15", 2)
    report.check(
        "an altitude below the lowest cannot be sent",
        not state["fly"]["go"] and state["fly"]["line"] == "Altitude: 20 to 500 m above home",
        state["fly"],
    )

    browser.keys("0")
    wait(browser, lambda s: s["fly"]["go"], 2)
    browser.press("#fly-here-go", HOLD_MS)
    state = wait(browser, lambda s: s["mode"] == "GUIDED" and s["mark"] and s["mark"]["sent"], 5)
    sent = [msg for msg in vehicle.vehicle_commands if msg.get_type() == "COMMAND_INT"]
    reposition = sent[-1] if sent else None
    report.check(
        "holding Fly here sends the vehicle there in GUIDED, and marks the point",
        reposition is not None
        and reposition.z == 150
        and reposition.frame == mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT
        and state["mode"] == "GUIDED"
        and not state["fly"]["open"]
        and state["mark"] == {"sent": True, "label": "GUIDED 150 m"},
        f"mode {state['mode']}, mark {state['mark']}, sent {reposition}",
    )

    browser.press("#map", button=2, dx=-80, dy=50)
    wait(browser, lambda s: s["fly"]["open"], 3)
    browser.keys(ESCAPE)
    state = wait(browser, lambda s: not s["fly"]["open"], 2)
    report.check(
        "Escape closes Fly here, and the point flown to stays marked",
        not state["fly"]["open"] and state["mark"] == {"sent": True, "label": "GUIDED 150 m"},
        f"open {state['fly']['open']}, mark {state['mark']}",
    )

    browser.press("#control-disarm", HOLD_MS)
    state = wait(browser, lambda s: s["armed"] == "DISARMED", 5)
    disarm = [
        msg
        for msg in vehicle.vehicle_commands
        if msg.command == mavlink.MAV_CMD_COMPONENT_ARM_DISARM and msg.param1 == 0
    ]
    report.check(
        "holding DISARM disarms",
        len(disarm) == 1 and state["armed"] == "DISARMED",
        f"{state['armed']}, {len(disarm)} disarm command(s) sent",
    )


def check_gozcu(
    browser: Browser, report: Report, vehicle: FakeAutopilot, camera: bool, shots: Path | None
):
    """One encounter: a drone closes in, a bird crosses, a climb is proposed and approved."""
    vehicle.mcu = FakeGozcu("encounter", time.monotonic(), mode=CONFIRM)
    state = wait(browser, lambda s: s["threat"] == "ADVISORY" and s["tracks"] > 0, 12)
    report.check(
        "Gözcü's threat and tracks show",
        state["threat"] == "ADVISORY" and state["tracks"] > 0,
        f"threat {state['threat']}, {state['tracks']} track(s)",
    )
    state = wait(browser, lambda s: len(s["rays"]) == 2, 8)  # the bird joins the drone
    report.check(
        "each track is a ray on the map, the target's amber",
        sorted(state["rays"]) == ["target warn", "track"],
        state["rays"],
    )
    if camera:
        seen = marks(browser)
        marked = seen["caution"] > MARKED_PX and seen["accent"] > MARKED_PX
        report.check("each track is marked on the picture, the target in amber", marked, seen)
        browser.press("#video-overlay")
        time.sleep(0.6)  # a few frames
        hidden, state = marks(browser), page(browser)
        browser.press("#video-overlay")
        report.check(
            "Overlay takes the marks off the picture, not the rays off the map",
            marked and sum(hidden.values()) == 0 and len(state["rays"]) == 2,
            f"{hidden}, rays {state['rays']}",
        )
    else:
        report.skip("each track is marked on the picture", "no camera stream")

    state = wait(browser, lambda s: s["deciding"], 8)
    proposal = state["proposal"].split(" ")[0]  # e.g. "#7"
    control = state["control"]
    report.check(
        "while a proposal waits, commands to the autopilot are held",
        state["deciding"]
        and control["line"] == f"Commands held: Gözcü's proposal {proposal[1:]} waits for an answer"
        and not (control["set"] or control["arm"] or control["disarm"]),
        f"proposal {state['proposal']!r}, {control}",
    )
    if shots:
        browser.screenshot(shots / "proposal.png")

    browser.press("#approve", HOLD_MS)
    state = wait(browser, lambda s: s["threat"] == "AVOID" and "accepted" in s["gozcuLine"], 5)
    report.check(
        "holding APPROVE answers the proposal, and Gözcü avoids",
        state["threat"] == "AVOID"
        and state["gozcuLine"] == f"approve proposal {proposal[1:]}: accepted"
        and state["proposal"].startswith(f"{proposal} CLIMB: APPROVED"),
        f"threat {state['threat']}, {state['gozcuLine']!r}, {state['proposal']!r}",
    )
    state = wait(browser, lambda s: "target crit" in s["rays"], 3)
    report.check(
        "in AVOID the target's ray turns red",
        "target crit" in state["rays"] and (state["rayLabel"] or "").startswith("TGT 3 drone τ"),
        f"rays {state['rays']}, label {state['rayLabel']!r}",
    )
    if camera:
        seen = marks(browser)
        report.check("in AVOID the target's mark turns red", seen["alarm"] > MARKED_PX, seen)
    report.check(
        "while Gözcü avoids, commands to the autopilot are held",
        state["control"]["line"] == "Commands held: Gözcü is avoiding",
        state["control"]["line"],
    )
    if shots:
        browser.screenshot(shots / "avoid.png")

    browser.press("[data-mode='0']", HOLD_MS)
    state = wait(browser, lambda s: s["gozcuMode"] == "WARN", 5)
    report.check(
        "holding a Gözcü mode button changes its mode",
        state["gozcuMode"] == "WARN" and state["gozcuLine"] == "mode WARN: accepted",
        f"mode {state['gozcuMode']}, {state['gozcuLine']!r}",
    )


def check_link(browser: Browser, report: Report, vehicle: FakeAutopilot) -> None:
    browser.press("#connection-button", HOLD_MS)  # Disconnect
    closed = wait(browser, lambda s: s["link"] == "DISCONNECTED", 5)
    browser.press("#connection-button", HOLD_MS)  # Connect
    opened = wait(browser, lambda s: s["link"] == "CONNECTED", 10)
    report.check(
        "holding Disconnect closes the link, and holding Connect opens it again",
        closed["link"] == "DISCONNECTED"
        and opened["link"] == "CONNECTED"
        and vehicle.connections == 2,
        f"{closed['link']}, then {opened['link']}; {vehicle.connections} connection(s)",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check the station's page in headless Firefox, against stand-ins."
    )
    parser.add_argument("--screenshots", type=Path, help="save screenshots in this folder")
    parser.add_argument("--firefox", default="firefox", help="the Firefox to use")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    firefox = shutil.which(args.firefox)
    if firefox is None:
        print(f"Firefox ({args.firefox}) is needed and was not found")
        return 2
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
    report = Report()
    with tempfile.TemporaryDirectory(prefix="gcs-ui-check-") as folder:
        vehicle = FakeAutopilot(position=POSITION)
        video_port = free_port(socket.SOCK_DGRAM)
        camera = start_camera(video_port)
        station = Station(Path(folder), vehicle.url, video_port if camera else None)
        browser = Browser(firefox)
        try:
            browser.open(station.url)
            check_start(browser, report, camera is not None)
            check_vehicle(browser, report, vehicle, args.screenshots)
            check_gozcu(browser, report, vehicle, camera is not None, args.screenshots)
            check_link(browser, report, vehicle)
            errors = [entry["text"] for entry in browser.log if entry.get("level") == "error"]
            report.check("the browser reported no errors", not errors, errors)
        finally:
            browser.close()
            log = station.close()
            if camera is not None:
                stop(camera)
            vehicle.close()
        broke = ("Traceback", "Exception in", "internal error")  # Python, uvicorn, MavlinkSource
        problems = [line for line in log.splitlines() if any(word in line for word in broke)]
        report.check("the station reported no errors of its own", not problems, problems)
    print(f"{report.passed} passed, {report.failed} failed, {report.skipped} skipped")
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
