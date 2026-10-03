"""Application version shared by the API, exports and the browser bundle."""

# Bump this value whenever the user-facing export contract changes.  It is
# deliberately kept outside the database so historical task snapshots do not
# get rewritten when the application is upgraded.
APP_VERSION = '2026.09.21.10'
