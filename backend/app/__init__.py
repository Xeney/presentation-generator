"""Версия сервиса: единый источник для API, релизного тега и манифестов.

Меняется вместе с тегом (`git tag`): см. `backend/pyproject.toml` и
`frontend/package.json` — тест `test_version_matches_manifests` следит за тем,
чтобы они не разъезжались.
"""

__version__ = "2.0.0"
