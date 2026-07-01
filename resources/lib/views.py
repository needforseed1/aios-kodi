import json
import os
import re

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs


ADDON = None
HANDLE = None
VIEW_SECTIONS = ()
VIEW_CANDIDATES = ()
AIOS_FORCED_VIEW_PROPERTY = "aios_forced_view"
AIOS_FORCED_VIEW_ID_PROPERTY = "aios_forced_view_id"
SKIN_CACHE_FILE = "skin_views.json"
_SKIN_CACHE = None


def init(addon, handle, view_sections, view_candidates):
    global ADDON, HANDLE, VIEW_SECTIONS, VIEW_CANDIDATES
    ADDON = addon
    HANDLE = handle
    VIEW_SECTIONS = view_sections
    VIEW_CANDIDATES = view_candidates


def setting(setting_id):
    return (ADDON.getSetting(setting_id) or "").strip()


def setting_int(setting_id, default):
    try:
        return int(setting(setting_id) or default)
    except ValueError:
        return default


def safe_int(value, default=0):
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def profile_path(filename):
    profile = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
    if not os.path.isdir(profile):
        os.makedirs(profile, exist_ok=True)
    return os.path.join(profile, filename)


def skin_cache_key():
    version = ""
    skin_addon = active_skin_addon()
    if skin_addon:
        try:
            version = skin_addon.getAddonInfo("version") or ""
        except Exception:
            version = ""
    return "%s|%s" % (xbmc.getSkinDir(), version)


def skin_cache():
    # Scanning every skin XML per directory render is far too slow on ARM
    # boxes, so scan results are memoized per skin id+version.
    global _SKIN_CACHE
    if _SKIN_CACHE is not None:
        return _SKIN_CACHE
    data = {}
    try:
        with open(profile_path(SKIN_CACHE_FILE), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or data.get("key") != skin_cache_key():
        data = {"key": skin_cache_key(), "type_labels": {}}
    if not isinstance(data.get("type_labels"), dict):
        data["type_labels"] = {}
    _SKIN_CACHE = data
    return data


def save_skin_cache():
    if _SKIN_CACHE is None:
        return
    path = profile_path(SKIN_CACHE_FILE)
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(_SKIN_CACHE, handle, separators=(",", ":"))
        os.replace(tmp_path, path)
    except OSError as exc:
        xbmc.log("AIOStreams could not save skin view cache: %s" % exc, xbmc.LOGWARNING)


def set_view(content="videos", view_setting="", cache_to_disc=True, fallback_view_setting=""):
    view_mode = view_mode_id(view_setting)
    if view_mode <= 0 and fallback_view_setting:
        view_mode = view_mode_id(fallback_view_setting)
    set_view_id(content, view_mode, cache_to_disc)


def set_view_id(content="videos", view_mode=0, cache_to_disc=True):
    xbmcplugin.setContent(HANDLE, content)
    hinted = set_skin_view_hint(view_mode)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=cache_to_disc)
    if view_mode and not (hinted and skin_view_hint_supported()):
        delay = max(0, min(setting_int("view_apply_delay_ms", 50), 2000))
        if delay:
            xbmc.sleep(delay)
        apply_view_mode(view_mode)


def set_skin_view_hint(view_mode):
    if view_mode <= 0:
        return False
    view_label = skin_view_mode_label(view_mode)
    if not view_label:
        return False
    try:
        xbmcplugin.setProperty(HANDLE, AIOS_FORCED_VIEW_PROPERTY, view_label)
        xbmcplugin.setProperty(HANDLE, AIOS_FORCED_VIEW_ID_PROPERTY, str(view_mode))
    except AttributeError:
        return False
    return True


def skin_view_hint_supported():
    cache = skin_cache()
    if isinstance(cache.get("hint_supported"), bool):
        return cache["hint_supported"]
    supported = scan_skin_view_hint_supported()
    cache["hint_supported"] = supported
    save_skin_cache()
    return supported


def scan_skin_view_hint_supported():
    skin_path = xbmcvfs.translatePath("special://skin/")
    try:
        for root, _dirs, files in os.walk(skin_path):
            if "Includes.xml" not in files:
                continue
            path = os.path.join(root, "Includes.xml")
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                    if AIOS_FORCED_VIEW_PROPERTY in handle.read():
                        return True
            except OSError:
                continue
    except OSError:
        return False
    return False


def skin_view_mode_label(view_mode):
    view_id = view_mode & 0xffff
    for candidate_id, label in skin_view_modes():
        if candidate_id == view_id and label:
            return label
    return skin_view_type_label(view_id)


def skin_view_type_label(view_id):
    if view_id <= 0:
        return ""
    cache = skin_cache()
    labels = cache["type_labels"]
    key = str(view_id)
    if key in labels:
        return str(labels[key])
    label = scan_skin_view_type_label(view_id)
    labels[key] = label
    save_skin_cache()
    return label


def scan_skin_view_type_label(view_id):
    skin_path = xbmcvfs.translatePath("special://skin/")
    skin_addon = active_skin_addon()
    try:
        for root, _dirs, files in os.walk(skin_path):
            for filename in files:
                if not filename.lower().endswith(".xml"):
                    continue
                label = view_type_label_from_file(os.path.join(root, filename), view_id, skin_addon)
                if label:
                    return label
    except OSError:
        return ""
    return ""


def view_type_label_from_file(path, view_id, skin_addon):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
    except OSError:
        return ""
    pattern = r"<control[^>]+id=[\"']%d[\"'][^>]*>" % view_id
    for match in re.finditer(pattern, text, flags=re.IGNORECASE):
        context = text[match.end():match.end() + 3000]
        viewtype = re.search(r"<viewtype\s+label=[\"']([^\"']+)[\"']", context, flags=re.IGNORECASE)
        if not viewtype:
            continue
        label = resolve_skin_label(viewtype.group(1), skin_addon)
        if label:
            return label
    return ""


def view_mode_id(view_setting):
    if not view_setting:
        return 0
    value = setting(view_setting)
    if value.startswith("raw:"):
        return safe_int(value[4:])
    configured = safe_int(value)
    if configured <= 0:
        return 0
    presets = {
        1: 50,
        2: 51,
        3: 52,
        4: 53,
        5: 54,
        6: 55,
        7: 56,
    }
    return presets.get(configured, configured)


def apply_view_mode(view_mode):
    if view_mode <= 0:
        return
    try:
        xbmc.executebuiltin("Container.SetViewMode(%d)" % view_mode, False)
    except TypeError:
        xbmc.executebuiltin("Container.SetViewMode(%d)" % view_mode)


def view_mode_value(setting_id):
    value = setting(setting_id)
    if value.startswith("raw:"):
        return value[4:]
    return value or "0"


def view_mode_setup(add_directory, fallback_art):
    xbmcplugin.setPluginCategory(HANDLE, "View Mode Setup")
    for setting_id, label, _content in VIEW_SECTIONS:
        add_directory("%s: %s" % (label, view_mode_value(setting_id)), {
            "action": "view_mode_candidates",
            "setting": setting_id,
        })
    set_view()


def view_mode_candidates(setting_id, add_directory, fallback_art, error):
    section = view_section(setting_id)
    if not section:
        error("Unknown view mode section")
        view_mode_setup(add_directory, fallback_art)
        return
    _setting_id, label, content = section
    xbmcplugin.setPluginCategory(HANDLE, "View Mode: " + label)
    add_directory("Disable forcing (Kodi default)", {
        "action": "view_mode_test",
        "setting": setting_id,
        "view": "0",
        "content": content,
    })
    add_directory("Enter custom raw view ID", {
        "action": "view_mode_custom",
        "setting": setting_id,
        "content": content,
    })
    detected = skin_view_modes()
    detected_ids = set(view_id for view_id, _label in detected)
    if detected:
        for view_id, view_label in detected:
            label_text = "%s (%s)" % (view_label, view_id) if view_label else "detected skin view ID %s" % view_id
            add_directory("Try " + label_text, {
                "action": "view_mode_test",
                "setting": setting_id,
                "view": str(view_id),
                "content": content,
            })
    for view_id, label_text in VIEW_CANDIDATES:
        if view_id in detected_ids:
            continue
        add_directory("Try " + label_text, {
            "action": "view_mode_test",
            "setting": setting_id,
            "view": str(view_id),
            "content": content,
        })
    set_view()


def view_mode_custom(setting_id, content, view_mode_candidates_func, view_mode_test_func):
    value = xbmcgui.Dialog().input("Raw Kodi view ID", defaultt=view_mode_value(setting_id), type=xbmcgui.INPUT_NUMERIC)
    if not value:
        view_mode_candidates_func(setting_id)
        return
    view_mode_test_func(setting_id, value, content)


def view_mode_test(setting_id, view_id, content, add_directory, notify, error, view_mode_setup_func):
    if not view_section(setting_id):
        error("Unknown view mode section")
        view_mode_setup_func()
        return
    if safe_int(view_id) <= 0:
        ADDON.setSetting(setting_id, "0")
        view_mode = 0
        notify("View forcing disabled")
    else:
        view_mode = safe_int(view_id)
        ADDON.setSetting(setting_id, "raw:%s" % view_mode)
        notify("Saved view ID %s" % view_mode)

    xbmcplugin.setPluginCategory(HANDLE, "Testing View %s" % view_mode_value(setting_id))
    for index in range(1, 9):
        add_directory("Sample item %s" % index, {"action": "noop"}, info={"title": "Sample item %s" % index, "plot": "Use Back to try another view ID."})
    set_view_id(content, view_mode)


def view_section(setting_id):
    for section in VIEW_SECTIONS:
        if section[0] == setting_id:
            return section
    return None


def skin_view_modes():
    cache = skin_cache()
    stored = cache.get("modes")
    if isinstance(stored, list):
        return [
            (safe_int(item[0]), str(item[1]))
            for item in stored
            if isinstance(item, (list, tuple)) and len(item) == 2
        ]
    modes = scan_skin_view_modes()
    cache["modes"] = [[view_id, label] for view_id, label in modes]
    save_skin_cache()
    return modes


def scan_skin_view_modes():
    modes = {}
    skin_path = xbmcvfs.translatePath("special://skin/")
    xml_path = os.path.join(skin_path, "xml")
    if not os.path.isdir(xml_path):
        xml_path = skin_path
    skin_addon = active_skin_addon()
    try:
        for root, _dirs, files in os.walk(xml_path):
            for filename in files:
                if not filename.lower().endswith(".xml"):
                    continue
                collect_view_modes_from_file(os.path.join(root, filename), modes, skin_addon)
    except OSError:
        return []
    return [(view_id, modes.get(view_id, "")) for view_id in sorted(modes)]


def active_skin_addon():
    try:
        return xbmcaddon.Addon(xbmc.getSkinDir())
    except Exception:
        return None


def collect_view_modes_from_file(path, modes, skin_addon):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
    except OSError:
        return
    for match in re.findall(r"<views>\s*([^<]+?)\s*</views>", text, flags=re.IGNORECASE):
        for value in re.findall(r"\d+", match):
            modes.setdefault(safe_int(value), "")
    for match in re.finditer(r"SetViewMode\((\d+)\)", text):
        view_id = safe_int(match.group(1))
        label = view_label_near(text, match.start(), skin_addon)
        if label and not modes.get(view_id):
            modes[view_id] = label
        else:
            modes.setdefault(view_id, "")


def view_label_near(text, position, skin_addon):
    before = text[max(0, position - 2000):position]
    after = text[position:min(len(text), position + 800)]
    for context in (before, after):
        labels = re.findall(r"<label[^>]*>\s*(.*?)\s*</label>", context, flags=re.IGNORECASE | re.DOTALL)
        for label in reversed(labels):
            resolved = resolve_skin_label(label, skin_addon)
            if resolved:
                return resolved
    return ""


def resolve_skin_label(value, skin_addon):
    text = re.sub(r"<[^>]+>", "", str(value)).strip()
    text = " ".join(text.split())
    if not text or "$INFO" in text or "$VAR" in text:
        return ""
    localize = re.search(r"\$LOCALIZE\[(\d+)\]", text)
    if localize:
        return localized_string(safe_int(localize.group(1)), skin_addon)
    if text.isdigit():
        return localized_string(safe_int(text), skin_addon)
    return text


def localized_string(label_id, skin_addon):
    if label_id <= 0:
        return ""
    if skin_addon:
        try:
            value = skin_addon.getLocalizedString(label_id)
            if value:
                return value
        except Exception:
            pass
    try:
        return xbmc.getLocalizedString(label_id) or ""
    except Exception:
        return ""
