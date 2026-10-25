#!/bin/sh
# TagMender in its Flatpak. Settings, keys, profiles, job history and undo are kept where a
# native install (the .deb, the AppImage, a clone) keeps them - ~/.config/tagmender and
# ~/.local/state/tagmender - so every way of running TagMender sees the same profiles and
# can undo the same jobs; the Flatpak has --filesystem=host, so these are the real folders.
export XDG_CONFIG_HOME="$HOME/.config"
export XDG_STATE_HOME="$HOME/.local/state"
export XDG_CACHE_HOME="$HOME/.cache"
exec python3 /app/lib/tagmender/tagmender.py "$@"
