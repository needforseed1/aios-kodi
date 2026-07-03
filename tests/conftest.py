"""Kodi API stubs so addon.py / service.py / views.py import under plain Python."""
import os
import sys
import tempfile
import types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_DIR = tempfile.mkdtemp(prefix="aios-kodi-tests-")


def _xbmc():
    mod = types.ModuleType("xbmc")
    mod.LOGDEBUG = 0
    mod.LOGINFO = 1
    mod.LOGWARNING = 2
    mod.LOGERROR = 3
    mod.log = lambda message, level=1: None
    mod.sleep = lambda ms: None
    mod.executebuiltin = lambda command, wait=False: None
    mod.getSkinDir = lambda: "skin.test"
    mod.getLocalizedString = lambda label_id: ""

    class Monitor:
        def abortRequested(self):
            return True

        def waitForAbort(self, seconds):
            return True

    class Player:
        def isPlaying(self):
            return False

    mod.Monitor = Monitor
    mod.Player = Player
    return mod


def _xbmcaddon():
    mod = types.ModuleType("xbmcaddon")

    class Addon:
        _settings = {}

        def __init__(self, addon_id=None):
            self.addon_id = addon_id

        def getSetting(self, setting_id):
            return Addon._settings.get(setting_id, "")

        def setSetting(self, setting_id, value):
            Addon._settings[setting_id] = str(value)

        def getAddonInfo(self, key):
            if key == "profile":
                return PROFILE_DIR
            if key == "path":
                return REPO_ROOT
            return "0.0.0"

        def getLocalizedString(self, label_id):
            return ""

        def openSettings(self):
            pass

    mod.Addon = Addon
    return mod


def _xbmcgui():
    mod = types.ModuleType("xbmcgui")
    mod.NOTIFICATION_INFO = "info"
    mod.NOTIFICATION_ERROR = "error"
    mod.INPUT_ALPHANUM = 0
    mod.INPUT_NUMERIC = 1

    class ListItem:
        def __init__(self, label="", label2="", path="", offscreen=False):
            self.label = label
            self.path = path
            self.properties = {}
            self.art = {}
            self.info = {}
            self.context_menu = []
            self.context_menu_replace = False

        def setProperty(self, key, value):
            self.properties[key] = value

        def setArt(self, art):
            self.art.update(art or {})

        def setInfo(self, info_type, info):
            self.info = dict(info or {})

        def setRating(self, rating_type, rating, votes=0, default=False):
            pass

        def setPath(self, path):
            self.path = path

        def setMimeType(self, mime):
            pass

        def setContentLookup(self, enabled):
            pass

        def addContextMenuItems(self, items, replaceItems=False):
            self.context_menu.extend(items or [])
            self.context_menu_replace = replaceItems

        def getVideoInfoTag(self):
            # No setters: forces the Kodi 19 setInfo fallback in tests.
            return object()

    class Dialog:
        def notification(self, *args, **kwargs):
            pass

        def ok(self, *args, **kwargs):
            pass

        def input(self, *args, **kwargs):
            return ""

    mod.ListItem = ListItem
    mod.Dialog = Dialog
    return mod


def _xbmcplugin():
    mod = types.ModuleType("xbmcplugin")
    mod.addDirectoryItem = lambda handle, url, item, isFolder=False: True
    mod.endOfDirectory = lambda handle, **kwargs: None
    mod.setContent = lambda handle, content: None
    mod.setPluginCategory = lambda handle, category: None
    mod.setResolvedUrl = lambda handle, succeeded, item: None
    mod.setProperty = lambda handle, key, value: None
    return mod


def _xbmcvfs():
    mod = types.ModuleType("xbmcvfs")
    mod.translatePath = lambda path: path
    return mod


for name, factory in (
    ("xbmc", _xbmc),
    ("xbmcaddon", _xbmcaddon),
    ("xbmcgui", _xbmcgui),
    ("xbmcplugin", _xbmcplugin),
    ("xbmcvfs", _xbmcvfs),
):
    if name not in sys.modules:
        sys.modules[name] = factory()

# addon.py reads sys.argv at import time the way Kodi invokes plugins.
sys.argv = ["plugin://plugin.video.aiostreams/", "1", ""]
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
