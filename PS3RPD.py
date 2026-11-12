# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "bs4",
#     "networkscan",
#     "pypresence",
#     "requests",
# ]
# ///
from pathlib import Path
from socket import socket, AF_INET, SOCK_DGRAM  # used to get host IP address
import json  # used for configuration
import re  # used for regular expressions
import os  # used to test if config exists
from html import unescape  # used to decode HTML entities in scraped names
from time import (
    sleep,
)  # used to add delay to mitigate rate limiting and webmanMOD memory consumption
import subprocess  # used to send ICMP ping packets to PS3
import platform  # used to get operating system of PC
import sqlite3  # used for getting image from database
import networkscan  # used for automatic obtaining IP address
from time import time
from urllib.parse import quote as url_quote
from bs4 import BeautifulSoup  # used for webpage scraping
import requests  # used to test if given IP belongs to PS3
from requests.exceptions import (
    ConnectionError,
)  # used to handle thrown errors on connecting to webpage
from pypresence import (
    Presence,
    InvalidPipe,
    InvalidID,
    DiscordNotFound,
)  # used for sending details to Discord


# --- webman HTML parsing helpers -------------------------------------------------
# webmanMOD writes game names with HTML entities (&amp;, &#039;) and serves its
# pages without a charset header, so requests falls back to ISO-8859-1 and turns
# UTF-8 degree signs into "Â°". Both problems are handled here.

RETRO_ISO_HREF = re.compile(r"/(dev_hdd0|dev_usb00[0-9])/(PSXISO|PS2ISO)")
PSXISO_HREF = re.compile(r"/(dev_hdd0|dev_usb00[0-9])/PSXISO")
PS2ISO_HREF = re.compile(r"/(dev_hdd0|dev_usb00[0-9])/PS2ISO")


def clean_text(value):
    """Decode HTML entities and normalize whitespace from a bs4 element or string."""
    if value is None:
        return ""
    if hasattr(value, "get_text"):
        value = value.get_text()
    text = unescape(str(value))
    text = text.replace("\xa0", " ")  # &nbsp;
    return re.sub(r"\s+", " ", text).strip()


def strip_app_version(name):
    """Drop a trailing PS3 app version such as ' 1.00' from a game name."""
    return re.sub(r"\s*\d+\.\d+\s*$", "", name).strip()


def parse_game_info(soup):
    """Return (title_id, name, kind) from a webman cpursx page.

    kind is 'ps3', 'retro' or 'xmb'.
    """
    title_link = soup.find("a", target="_blank")
    if title_link is not None:
        title_id = clean_text(title_link)
        name = ""
        for sibling in title_link.next_siblings:
            text = clean_text(sibling)
            if text:
                name = strip_app_version(text)
                break
        return title_id, name, "ps3"

    if soup.find("a", href=RETRO_ISO_HREF) is not None:
        name = "PlayStation 1/2"
        for link in soup.find_all("a", href=RETRO_ISO_HREF):
            text = clean_text(link)
            if text:
                name = text
                break
        return None, name, "retro"

    return None, "XMB", "xmb"


# --- SteamGridDB art provider ---------------------------------------------------

SGDB_BASE = "https://www.steamgriddb.com/api/v2"
# Cache game_name -> grid URL so we only hit the API once per title per session.
_sgdb_cache: dict[str, str | None] = {}


def sgdb_get_grid(game_name: str, api_key: str) -> str | None:
    """Search SteamGridDB for *game_name* and return the first grid image URL, or None.

    Results are cached in-memory for the lifetime of the process.
    """
    if game_name in _sgdb_cache:
        return _sgdb_cache[game_name]

    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        # Step 1: search for the game
        search_url = f"{SGDB_BASE}/search/autocomplete/{url_quote(game_name)}"
        resp = requests.get(search_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"sgdb_get_grid(): search returned {resp.status_code}")
            _sgdb_cache[game_name] = None
            return None

        data = resp.json()
        games = data.get("data", [])
        if not games:
            print(f"sgdb_get_grid(): no results for '{game_name}'")
            _sgdb_cache[game_name] = None
            return None

        game_id = games[0].get("id")
        if game_id is None:
            _sgdb_cache[game_name] = None
            return None

        # Step 2: get grids for this game
        grids_url = f"{SGDB_BASE}/grids/game/{game_id}"
        resp = requests.get(grids_url, headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"sgdb_get_grid(): grids returned {resp.status_code}")
            _sgdb_cache[game_name] = None
            return None

        grids = resp.json().get("data", [])
        if not grids:
            print(f"sgdb_get_grid(): no grids for game_id={game_id}")
            _sgdb_cache[game_name] = None
            return None

        url = grids[0].get("url") or grids[0].get("thumb")
        _sgdb_cache[game_name] = url
        if url:
            print(f"sgdb_get_grid(): found grid for '{game_name}'")
        return url

    except Exception as e:
        print(f"sgdb_get_grid(): error - {e}")
        _sgdb_cache[game_name] = None
        return None


# --- Fallback art: GameTDB (original provider) ----------------------------------

GAMETDB_REGION_MAP = {
    "A": "ZH",
    "E": "EN",
    "H": "US",
    "J": "JA",
    "K": "KO",
    "U": "US",
}


def gametdb_get_cover(title_id: str) -> str | None:
    """Return a GameTDB cover URL for *title_id*, or None on failure."""
    if not title_id or len(title_id) < 3:
        return None
    region = GAMETDB_REGION_MAP.get(title_id[2])
    if not region:
        print(f"gametdb_get_cover(): unexpected region char '{title_id[2]}'")
        return None
    url = f"https://art.gametdb.com/ps3/cover/{region}/{title_id}.jpg"
    try:
        resp = requests.get(url, headers={"User-Agent": "PS3RPD/2.0"}, timeout=10)
        if resp.status_code == 200:
            print("gametdb_get_cover(): found")
            return url
        else:
            print(f"gametdb_get_cover(): {resp.status_code} for {url}")
            return None
    except Exception as e:
        print(f"gametdb_get_cover(): error - {e}")
        return None


# --- Unified art resolver --------------------------------------------------------

def resolve_art(game_name: str, title_id: str | None, config: dict) -> str:
    """Try every art source in order; always return *something* (never raise).

    Priority:
      1. SteamGridDB (if api key is configured)
      2. Local psimg.db (if present)
      3. GameTDB (if title_id is available)
      4. Discord dev-app asset name (title_id lowercase)
      5. Bare fallback string "ps3"
    """
    # SteamGridDB
    api_key = config.get("steamgriddb_api_key", "")
    if api_key and game_name:
        url = sgdb_get_grid(game_name, api_key)
        if url:
            return url

    # Local DB
    if title_id and os.path.isfile("psimg.db"):
        try:
            con = sqlite3.connect("psimg.db")
            cur = con.cursor()
            result = cur.execute(
                "SELECT * FROM PS3 WHERE titleID == ?", (title_id.upper(),)
            ).fetchall()
            con.close()
            if result:
                img = re.sub(r"[ \n]", "", result[0][2])
                print("resolve_art(): using psimg.db")
                return img
        except Exception as e:
            print(f"resolve_art(): psimg.db error - {e}")

    # GameTDB
    if title_id:
        url = gametdb_get_cover(title_id)
        if url:
            return url

    # Discord dev-app fallback
    if title_id:
        return title_id.lower()

    return "ps3"


# ---------------------------------------------------------------------------------

default_config = {
    "ip": "",
    "client_id": 780389261870235650,
    "wait_seconds": 35,
    "show_temp": True,
    "retro_covers": False,
    "show_elapsed": True,
    "hibernate_seconds": 600,
    "ip_prompt": True,
    "show_timer": True,
    "prefer_dev_app": False,
    "use_appname": False,
    "steamgriddb_api_key": "",
}


class PrepWork:
    config_path = Path("ps3rpdconfig.txt")

    def __init__(self):
        self.RPC = None
        self.config = {}

    def read_config(self):
        if self.config_path.is_file():
            with self.config_path.open(mode="r") as f:
                self.config = json.load(f)
            # Merge any new default keys into an existing config
            for key, val in default_config.items():
                if key not in self.config:
                    self.config[key] = val
            self.config["wait_seconds"] = max(15, self.config["wait_seconds"])
            if not self.test_for_webman(self.config["ip"]) and self.config["ip_prompt"]:
                print("PS3 cannot be reached via the IP saved in the config file.")
                self.prompt_user()
        else:
            self.config = dict(default_config)
            self.prompt_user()

    def prompt_user(self):
        choice = "placeholder"
        accepted = ["a", "m"]
        print("\nGet PS3's IP address automatically, or manually?")
        while (
            choice[0].lower() not in accepted
        ):  # test if first character of choice is in array
            choice = input('Please enter either "A", or "M": ')
        if choice[0].lower() == "a":
            self.grab_host_network()
        elif choice[0].lower() == "m":
            self.get_IP_from_user()
        else:
            exit("Unexpected input")

    def grab_host_network(self):
        hostNetwork = None
        try:
            tempSock = socket(AF_INET, SOCK_DGRAM)
            tempSock.connect(("8.8.8.8", 80))
            hostNetwork = tempSock.getsockname()[0]
            tempSock.close()
        except Exception as e:
            print(f'Error while getting host network. "{e}"')

        if hostNetwork is not None:
            hostNetwork = re.search(r"^(.*)\.", hostNetwork).group(0)
            print(f'expected network is "{hostNetwork}"')
            self.scan_network(hostNetwork)

    def scan_network(self, my_network):
        found = False
        my_network += "0/24"
        while True:
            my_scan = networkscan.Networkscan(my_network)
            my_scan.run()
            print("Completed network scan.")
            print(my_scan.list_of_hosts_found)
            for i in range(len(my_scan.list_of_hosts_found)):
                if self.test_for_webman(my_scan.list_of_hosts_found[i]):
                    self.save_config(my_scan.list_of_hosts_found[i])
                    found = True
                    break
            if found is True:
                break
            else:
                print("PS3 not found on network, waiting 20 seconds before retry")
                sleep(20)

    def get_IP_from_user(self):
        while True:
            ip = input("Enter PS3's IP address: ")
            if self.test_for_webman(ip):
                self.save_config(ip)
                break

    def test_for_webman(self, ip):
        response = None
        url = f"http://{ip}"
        try:
            response = requests.get(url, headers=http_headers, timeout=10)
        except (ConnectionError, requests.exceptions.Timeout):
            print(f'No webpage found on "{ip}"')
            return False
        if response is not None:
            soup = BeautifulSoup(response.text, "html.parser")
            pageTitle = str(soup.find("title"))
            if "wMAN" in pageTitle or "webMAN" in pageTitle:
                print(f'given IP "{ip}" belongs to webman.')
                return True
            else:
                print(
                    f'WebmanMOD not found on "{ip}", reports "{pageTitle}". If you believe this is an error, please contact the developer. '
                    f"Please ensure the PS3 is turned on, has webmanMOD installed and running, "
                    f"and is connected to the same network as the PC."
                )
                return False

    def save_config(self, valid_ip):
        self.config["ip"] = valid_ip
        with self.config_path.open(mode="w+") as f:
            json.dump(self.config, f, indent=4)

    def connect_to_discord(self):
        while True:
            try:
                self.RPC = Presence(self.config["client_id"])
                self.RPC.connect()
                print("connected to Discord client")
                break
            except DiscordNotFound as e:
                print(f'could not find Discord client running. "{e}"')
                sleep(20)


class GatherDetails:
    def __init__(self):
        self.soup = None
        self.thermalData = None
        self.name = None
        self.titleID = None
        self.image = None
        self.isRetroGame = False

    def ping_PS3(self):
        if platform.system().lower() == "windows":
            command = ["ping", "-n", "5", prepWork.config["ip"]]
        else:
            command = ["ping", "-c", "5", prepWork.config["ip"]]
        with open(os.devnull, "w") as DEVNULL:
            try:
                subprocess.check_call(command, stdout=DEVNULL)
                return True
            except subprocess.CalledProcessError:
                return False

    def get_html(self):
        url = f"http://{prepWork.config['ip']}/cpursx.ps3?/sman.ps3"
        if not self.ping_PS3():
            return False

        try:
            response = requests.get(url, headers=http_headers, timeout=10)
            # webman serves pages without a charset header, so requests guesses
            # ISO-8859-1 and mangles UTF-8 (degree signs become "Â°").
            response.encoding = "utf-8"
            self.soup = BeautifulSoup(response.text, "html.parser")
            return True
        except (ConnectionError, requests.exceptions.Timeout) as e:
            print(f'get_html():  webman not found. "{e}".')
            return False

    def get_thermals(self):
        thermalData = str(self.soup.find("a", href="/cpursx.ps3?up"))
        cpu = re.search(r"CPU(.+?)C", thermalData)
        rsx = re.search(r"RSX(.+?)C", thermalData)
        try:
            cpu = cpu.group(0)
            rsx = rsx.group(0)
            self.thermalData = f"{cpu} | {rsx}"
            print(f"get_thermals():     {self.thermalData}")
        except AttributeError:
            print(
                f"get_thermals(): could not find html for thermal data, has webmanMOD been updated since {wmanVer}?"
            )

    def decide_game_type(self):
        self.isRetroGame = False
        self.titleID, self.name, kind = parse_game_info(self.soup)
        if kind == "ps3":
            print("decide_game_type():  PS3 Game or Homebrew")
            self.isRetroGame = False
            self.get_PS3_details()
        elif kind == "retro":
            self.isRetroGame = True
            print("decide_game_type():  Retro")
            self.get_retro_details()
        else:
            print("decide_game_type():  XMB")
            self.image = "xmb"
            self.titleID = None

    def get_PS3_details(self):
        print(f"get_PS3_details():  {self.titleID} | {self.name}")
        if prevTitle != self.titleID:
            self.image = resolve_art(self.name, self.titleID, prepWork.config)
            print(f"get_PS3_details():  image = {self.image}")

    def get_retro_details(self):
        print(f"get_retro_details(): {self.name}")
        # Try SteamGridDB for retro titles too
        self.image = resolve_art(self.name, None, prepWork.config)
        print(f"get_retro_details(): image = {self.image}")


http_headers = {"User-Agent": "Mozilla/5.0"}
wmanVer = "1.47.48"


def main():
    global prepWork, gatherDetails, timer, prevTitle, closed

    prepWork = PrepWork()
    prepWork.read_config()
    prepWork.connect_to_discord()
    closed = False
    gatherDetails = GatherDetails()
    timer = None
    if prepWork.config["show_timer"]:
        timer = time()
    prevTitle = ""

    if prepWork.config["ip"] is None:
        exit("script failed to execute critical functions.")

    while True:
        if not gatherDetails.get_html():
            if gatherDetails.isRetroGame is True:
                print(
                    f"PS2 game previously mounted, keeping RPC active and waiting {prepWork.config['wait_seconds']} seconds"
                )
                sleep(prepWork.config["wait_seconds"])
            else:
                print(
                    f"PS3 not found on network, closing RPC and hibernating {prepWork.config['hibernate_seconds']} seconds."
                )
                if not closed:
                    prepWork.RPC.clear()
                prepWork.RPC.close()
                closed = True
                sleep(float(prepWork.config["hibernate_seconds"]))
        else:
            print("")
            if closed:
                prepWork.connect_to_discord()
                timer = time()
                closed = False
            if prepWork.config["show_temp"]:
                gatherDetails.get_thermals()
            gatherDetails.decide_game_type()

            if prepWork.config["use_appname"]:
                try:
                    prepWork.RPC.update(
                        details=gatherDetails.name,
                        state=gatherDetails.thermalData,
                        large_image=gatherDetails.image,
                        large_text=gatherDetails.titleID,
                        start=timer,
                    )
                except (InvalidPipe, InvalidID):
                    prepWork.RPC.close()
                    prepWork.connect_to_discord()
            else:
                try:
                    prepWork.RPC.update(
                        name=gatherDetails.name,
                        details=gatherDetails.thermalData,
                        large_image=gatherDetails.image,
                        large_text=gatherDetails.titleID,
                        start=timer,
                    )
                except (InvalidPipe, InvalidID):
                    prepWork.RPC.close()
                    prepWork.connect_to_discord()
            prevTitle = gatherDetails.titleID
            sleep(prepWork.config["wait_seconds"])


if __name__ == "__main__":
    main()
