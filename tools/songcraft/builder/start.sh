#!/bin/bash
# Entry script invoked by ~/Denmoku.app via app-launcher.
# server.py binds the preferred port (8773, dynamic fallback if taken) and
# writes the actual bound URL into .app-url after binding, so nothing here
# needs to know the port.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

# Self-heal from a previous Force-Quit that left a python child orphaned
# on the preferred port. Non-fatal if the helper isn't installed.
if [ -x "$HOME/.local/bin/reap-orphan-port" ]; then
    "$HOME/.local/bin/reap-orphan-port" 8773 || true
fi

# Hold Option while launching the .app to set JL_PRIVATE=1; that routes
# the auto-open through a Chrome incognito app window instead of the
# default browser.
if [ "$JL_PRIVATE" = "1" ] && [ -x "$HOME/.local/bin/open-private" ]; then
    OPEN_URL="$HOME/.local/bin/open-private"
else
    OPEN_URL="/usr/bin/open"
fi

# Give server.py a second to bind and write .app-url, then open whatever
# it wrote (fall back to the preferred port if the file isn't there yet).
(
    sleep 1
    if [ -s .app-url ]; then
        url=$(cat .app-url)
    else
        url="http://127.0.0.1:8773/"
    fi
    "$OPEN_URL" "$url"
) &

exec python3 server.py
