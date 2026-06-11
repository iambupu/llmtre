"""
功能：覆盖 mod media a2 的回归测试。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from flask import Flask

from web_api.blueprints import mods as mods_blueprint_module


def _client_for_mod_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, Path, Path]:
    """
    功能：构造隔离的 MOD 媒体 API 测试客户端。
    入参：tmp_path（Path）：临时根目录；monkeypatch（pytest.MonkeyPatch）：路径替换工具。
    出参：tuple[Any, Path, Path]，FlaskClient、mods 根目录、注册表路径。
    异常：目录或 Flask 初始化失败时向上抛出。
    """
    mods_root = tmp_path / "mods"
    config_root = tmp_path / "config"
    mods_root.mkdir()
    config_root.mkdir()
    registry_path = config_root / "mod_registry.yml"
    monkeypatch.setattr(mods_blueprint_module, "MODS_ROOT", str(mods_root))
    monkeypatch.setattr(mods_blueprint_module, "REGISTRY_PATH", str(registry_path))

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(mods_blueprint_module.mods_blueprint)
    return app.test_client(), mods_root, registry_path


def test_mod_media_api_lists_and_serves_declared_media(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 MOD 媒体 API 只列出并服务已启用 MOD 的已声明媒体。
    入参：tmp_path（Path）：临时目录；monkeypatch（pytest.MonkeyPatch）：路径替换工具。
    出参：None。
    异常：断言失败表示媒体路由、MIME、启用态或未声明路径保护回归。
    """
    client, mods_root, registry_path = _client_for_mod_media(tmp_path, monkeypatch)
    media_dir = mods_root / "media_mod" / "assets" / "images"
    media_dir.mkdir(parents=True)
    (media_dir / "hero.png").write_bytes(b"hero")
    disabled_dir = mods_root / "disabled_mod" / "assets" / "audio"
    disabled_dir.mkdir(parents=True)
    (disabled_dir / "theme.mp3").write_bytes(b"theme")
    registry_path.write_text(
        yaml.safe_dump(
            {
                "global_settings": {"default_conflict_strategy": "smart_merge"},
                "active_mods": [
                    {
                        "mod_id": "media_mod",
                        "enabled": True,
                        "priority": 50,
                        "media_manifest": {
                            "hero": {
                                "media_id": "hero",
                                "kind": "image",
                                "src": "images/hero.png",
                                "mime_type": "image/png",
                                "alt": "封面",
                                "size_bytes": 4,
                                "playback": {
                                    "mode": "manual",
                                    "controls": True,
                                    "muted": False,
                                    "preload": "metadata",
                                    "volume": 1,
                                    "start_time_seconds": 0,
                                },
                            }
                        },
                    },
                    {
                        "mod_id": "disabled_mod",
                        "enabled": False,
                        "priority": 10,
                        "media_manifest": {
                            "theme": {
                                "media_id": "theme",
                                "kind": "audio",
                                "src": "audio/theme.mp3",
                                "mime_type": "audio/mpeg",
                            }
                        },
                    },
                ],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    listed = client.get("/api/mods/media")
    media_response = client.get("/api/mods/media_mod/media/images/hero.png")
    undeclared_response = client.get("/api/mods/media_mod/media/images/missing.png")
    disabled_response = client.get("/api/mods/disabled_mod/media/audio/theme.mp3")

    listed_body = listed.get_json()
    assert listed.status_code == 200
    assert [item["media_id"] for item in listed_body["media"]] == ["hero"]
    assert listed_body["media"][0]["url"] == "/api/mods/media_mod/media/images/hero.png"
    assert listed_body["media"][0]["playback"]["mode"] == "manual"
    assert media_response.status_code == 200
    assert media_response.get_data() == b"hero"
    assert media_response.mimetype == "image/png"
    assert undeclared_response.status_code == 404
    assert undeclared_response.get_json()["error"]["code"] == "MEDIA_NOT_FOUND"
    assert disabled_response.status_code == 404
    assert disabled_response.get_json()["error"]["code"] == "MOD_NOT_FOUND"


def test_mod_media_api_rejects_path_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    功能：验证 MOD 媒体 API 拒绝越界路径，即使注册表中存在合法媒体。
    入参：tmp_path（Path）：临时目录；monkeypatch（pytest.MonkeyPatch）：路径替换工具。
    出参：None。
    异常：断言失败表示路径穿越保护回归。
    """
    client, mods_root, registry_path = _client_for_mod_media(tmp_path, monkeypatch)
    media_dir = mods_root / "media_mod" / "assets" / "images"
    media_dir.mkdir(parents=True)
    (media_dir / "hero.png").write_bytes(b"hero")
    registry_path.write_text(
        yaml.safe_dump(
            {
                "active_mods": [
                    {
                        "mod_id": "media_mod",
                        "enabled": True,
                        "media_manifest": {
                            "hero": {
                                "media_id": "hero",
                                "kind": "image",
                                "src": "images/hero.png",
                                "mime_type": "image/png",
                            }
                        },
                    }
                ]
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    response = client.get("/api/mods/media_mod/media/%2e%2e/secret.png")

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "INVALID_ARGUMENT"


def test_openapi_mod_media_declares_all_supported_mime_types() -> None:
    """
    功能：验证 OpenAPI 声明覆盖 MOD 媒体白名单中的视频与音频 MIME。
    入参：无。
    出参：None。
    异常：断言失败表示 MOD 媒体服务能力与 API 契约漂移。
    """
    spec = yaml.safe_load(Path("config/api/openapi.yaml").read_text(encoding="utf-8"))
    content = set(
        spec["paths"]["/api/mods/{mod_id}/media/{media_path}"]["get"]["responses"]["200"]["content"]
    )

    assert {"video/quicktime", "audio/mp4", "audio/flac"} <= content
