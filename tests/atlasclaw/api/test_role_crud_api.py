# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""Tests for role CRUD API endpoints."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncGenerator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlasclaw.api.api_routes import router as api_router
from app.atlasclaw.api.routes import APIContext, create_router, set_api_context
from app.atlasclaw.auth.config import AuthConfig
from app.atlasclaw.auth.middleware import setup_auth_middleware
from app.atlasclaw.channels import ChannelRegistry
from app.atlasclaw.channels.handlers import WebSocketHandler
from app.atlasclaw.db import get_db_session
from app.atlasclaw.db.database import DatabaseConfig, DatabaseManager, init_database
from app.atlasclaw.db.models import RoleModel
from app.atlasclaw.auth.guards import resolve_authorization_context
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.db.orm.role import RoleService
from app.atlasclaw.db.orm.service_provider_config import ServiceProviderConfigService
from app.atlasclaw.db.orm.user import UserService
from app.atlasclaw.db.schemas import RoleCreate, ServiceProviderConfigCreate, UserCreate, UserUpdate
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillMetadata, SkillRegistry
from app.atlasclaw.tools.registration import register_builtin_tools


_test_db_manager: DatabaseManager = None


async def _test_get_db_session() -> AsyncGenerator[AsyncSession, None]:
    global _test_db_manager
    async with _test_db_manager.get_session() as session:
        yield session


def _build_client(
    tmp_path: Path,
    auth_config: AuthConfig,
    *,
    with_builtin_tools: bool = False,
) -> TestClient:
    registry = SkillRegistry()
    if with_builtin_tools:
        register_builtin_tools(registry)

    async def echo_skill(message: str = "ok") -> dict[str, str]:
        return {"echo": message}

    registry.register(
        SkillMetadata(name="echo-skill", description="Echo test skill"),
        echo_skill,
    )

    ctx = APIContext(
        session_manager=SessionManager(agents_dir=str(tmp_path / 'agents')),
        session_queue=SessionQueue(),
        skill_registry=registry,
    )
    set_api_context(ctx)

    app = FastAPI()
    app.state.config = SimpleNamespace(auth=auth_config)
    setup_auth_middleware(app, auth_config)
    app.include_router(create_router())
    app.include_router(api_router)
    app.dependency_overrides[get_db_session] = _test_get_db_session
    return TestClient(app)


def _init_database_sync(tmp_path: Path):
    global _test_db_manager

    async def _init():
        global _test_db_manager
        db_path = tmp_path / 'test_role_crud.db'
        _test_db_manager = await init_database(DatabaseConfig(db_type='sqlite', sqlite_path=str(db_path)))
        await _test_db_manager.create_tables()
        async with _test_db_manager.get_session() as session:
            await UserService.create(
                session,
                UserCreate(
                    username='admin',
                    password='adminpass123',
                    display_name='Test Admin',
                    email='admin@test.com',
                    roles={'admin': True},
                    auth_type='local',
                    is_active=True,
                ),
            )
            await UserService.create(
                session,
                UserCreate(
                    username='regularuser',
                    password='userpass123',
                    display_name='Regular User',
                    email='user@test.com',
                    roles={},
                    auth_type='local',
                    is_active=True,
                ),
            )
        return _test_db_manager

    return asyncio.run(_init())


def _cleanup_manager(manager):
    asyncio.run(manager.close())


@contextmanager
def _registered_test_channels(*channel_types: str):
    original_handlers = dict(ChannelRegistry._handlers)
    original_instances = dict(ChannelRegistry._instances)
    original_connections = dict(ChannelRegistry._connections)
    ChannelRegistry._handlers.clear()
    ChannelRegistry._instances.clear()
    ChannelRegistry._connections.clear()
    for channel_type in channel_types:
        ChannelRegistry.register(channel_type, WebSocketHandler)

    try:
        yield
    finally:
        ChannelRegistry._handlers.clear()
        ChannelRegistry._handlers.update(original_handlers)
        ChannelRegistry._instances.clear()
        ChannelRegistry._instances.update(original_instances)
        ChannelRegistry._connections.clear()
        ChannelRegistry._connections.update(original_connections)


def _get_auth_config() -> AuthConfig:
    return AuthConfig(
        provider='local',
        jwt={
            'secret_key': 'test-secret-key-for-testing',
            'issuer': 'atlasclaw-test',
            'header_name': 'AtlasClaw-Authenticate',
            'cookie_name': 'AtlasClaw-Authenticate',
            'expires_minutes': 60,
        },
    )


@pytest.fixture(autouse=True)
def _registered_websocket_channel():
    with _registered_test_channels('websocket'):
        yield


def _login_as(client: TestClient, username: str, password: str) -> str:
    response = client.post('/api/auth/local/login', json={'username': username, 'password': password})
    assert response.status_code == 200, f'Login failed: {response.json()}'
    return response.json()['token']


class TestRoleCRUDAPI:
    """Tests for role management endpoints."""

    def test_channel_permissions_require_explicit_allowed_true(self, tmp_path):
        permissions = RoleService.normalize_permissions({
            'channels': {
                'channel_permissions': [
                    {'channel_type': 'websocket', 'channel_name': 'WebSocket'},
                    {'channel_type': 'feishu', 'channel_name': 'Feishu', 'allowed': True},
                ],
            },
        })

        permissions_by_type = {
            entry['channel_type']: entry
            for entry in permissions['channels']['channel_permissions']
        }
        assert permissions_by_type['websocket']['allowed'] is False
        assert permissions_by_type['feishu']['allowed'] is True

    def test_list_roles_auto_seeds_builtin_roles(self, tmp_path):
        manager = None

        with _registered_test_channels('websocket'):
            try:
                manager = _init_database_sync(tmp_path)

                async def _seed_provider():
                    async with _test_db_manager.get_session() as session:
                        await ServiceProviderConfigService.create(
                            session,
                            ServiceProviderConfigCreate(
                                provider_type='smartcmp',
                                instance_name='default',
                                config={
                                    'base_url': 'https://cmp.example.com',
                                    'auth_type': 'user_token',
                                },
                                is_active=True,
                            ),
                        )

                asyncio.run(_seed_provider())
                client = _build_client(tmp_path, _get_auth_config())
                token = _login_as(client, 'admin', 'adminpass123')

                response = client.get('/api/roles?page=1&page_size=20', headers={'AtlasClaw-Authenticate': token})

                assert response.status_code == 200
                payload = response.json()
                identifiers = {role['identifier'] for role in payload['roles']}
                assert {'admin', 'user', 'viewer'}.issubset(identifiers)
                roles_by_identifier = {role['identifier']: role for role in payload['roles']}
                assert roles_by_identifier['admin']['permissions']['agent_configs']['view'] is True
                assert roles_by_identifier['admin']['permissions']['providers']['module_permissions']['manage_permissions'] is True
                assert roles_by_identifier['admin']['permissions']['channels']['module_permissions']['manage_permissions'] is True
                admin_channel_types = {
                    entry['channel_type']
                    for entry in roles_by_identifier['admin']['permissions']['channels']['channel_permissions']
                    if entry['allowed'] is True
                }
                assert admin_channel_types == {'websocket'}
                admin_provider_keys = {
                    (entry['provider_type'], entry['instance_name'])
                    for entry in roles_by_identifier['admin']['permissions']['providers']['provider_permissions']
                    if entry['allowed'] is True
                }
                assert admin_provider_keys == {('jira', 'dev'), ('smartcmp', 'default')}
                user_provider_keys = {
                    (entry['provider_type'], entry['instance_name'])
                    for entry in roles_by_identifier['user']['permissions']['providers']['provider_permissions']
                    if entry['allowed'] is True
                }
                assert user_provider_keys == admin_provider_keys
                user_channel_types = {
                    entry['channel_type']
                    for entry in roles_by_identifier['user']['permissions']['channels']['channel_permissions']
                    if entry['allowed'] is True
                }
                assert user_channel_types == admin_channel_types
                assert roles_by_identifier['user']['permissions']['skills']['module_permissions']['view'] is False
                assert roles_by_identifier['user']['permissions']['agent_configs']['view'] is False
                assert roles_by_identifier['user']['permissions']['provider_configs']['view'] is False
                assert roles_by_identifier['viewer']['permissions']['agent_configs']['view'] is False
                assert roles_by_identifier['viewer']['permissions']['provider_configs']['view'] is False
                assert roles_by_identifier['viewer']['permissions']['model_configs']['view'] is False
                assert roles_by_identifier['viewer']['permissions']['providers']['provider_permissions'] == []
            finally:
                if manager is not None:
                    _cleanup_manager(manager)

    def test_list_roles_preserves_builtin_permissions_while_syncing_metadata(self, tmp_path):
        manager = _init_database_sync(tmp_path)

        async def _seed_stale_viewer():
            async with _test_db_manager.get_session() as session:
                session.add(
                    RoleModel(
                        name='Viewer',
                        identifier='viewer',
                        description='stale viewer',
                        permissions={
                            'skills': {'module_permissions': {'view': True}, 'skill_permissions': []},
                            'agent_configs': {'view': True},
                            'provider_configs': {'view': True},
                            'model_configs': {'view': True},
                        },
                        is_builtin=True,
                        is_active=False,
                    )
                )
                await session.flush()

        asyncio.run(_seed_stale_viewer())

        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, 'admin', 'adminpass123')

        response = client.get('/api/roles?page=1&page_size=20', headers={'AtlasClaw-Authenticate': token})

        assert response.status_code == 200
        roles_by_identifier = {role['identifier']: role for role in response.json()['roles']}
        viewer = roles_by_identifier['viewer']
        assert viewer['description'] == 'Read-only role for audit and oversight workflows.'
        assert viewer['is_active'] is True
        assert viewer['permissions']['agent_configs']['view'] is True
        assert viewer['permissions']['provider_configs']['view'] is True
        assert viewer['permissions']['model_configs']['view'] is True
        assert viewer['permissions']['channels']['module_permissions']['manage_permissions'] is False
        assert viewer['permissions']['skills']['module_permissions']['view'] is True

        _cleanup_manager(manager)

    def test_builtin_role_sync_preserves_existing_runtime_access_permissions(self, tmp_path):
        manager = None

        async def _seed_user_role_with_existing_channel_choice():
            async with _test_db_manager.get_session() as session:
                await ServiceProviderConfigService.create(
                    session,
                    ServiceProviderConfigCreate(
                        provider_type='smartcmp',
                        instance_name='default',
                        config={
                            'base_url': 'https://cmp.example.com',
                            'auth_type': 'user_token',
                        },
                        is_active=True,
                    ),
                )
                session.add(
                    RoleModel(
                        name='Standard User',
                        identifier='user',
                        description='stale user',
                        permissions={
                            **RoleService.normalize_permissions({}),
                            'channels': {
                                'module_permissions': {'manage_permissions': False},
                                'channel_permissions': [
                                    {
                                        'channel_type': 'websocket',
                                        'channel_name': 'WebSocket',
                                        'allowed': False,
                                    },
                                    {
                                        'channel_type': 'ghost',
                                        'channel_name': 'Ghost',
                                        'allowed': True,
                                    },
                                ],
                            },
                        },
                        is_builtin=True,
                        is_active=True,
                    )
                )
                await session.flush()

        with _registered_test_channels('websocket', 'matrix'):
            try:
                manager = _init_database_sync(tmp_path)
                asyncio.run(_seed_user_role_with_existing_channel_choice())
                client = _build_client(tmp_path, _get_auth_config())
                token = _login_as(client, 'admin', 'adminpass123')

                response = client.get('/api/roles?page=1&page_size=20', headers={'AtlasClaw-Authenticate': token})

                assert response.status_code == 200
                user_role = next(role for role in response.json()['roles'] if role['identifier'] == 'user')
                channels_by_type = {
                    entry['channel_type']: entry
                    for entry in user_role['permissions']['channels']['channel_permissions']
                }
                assert channels_by_type['websocket']['allowed'] is False
                assert channels_by_type['ghost']['allowed'] is True
                assert 'matrix' not in channels_by_type
                provider_keys = {
                    (entry['provider_type'], entry['instance_name'])
                    for entry in user_role['permissions']['providers']['provider_permissions']
                    if entry['allowed'] is True
                }
                assert provider_keys == set()
            finally:
                if manager is not None:
                    _cleanup_manager(manager)

    def test_create_update_and_delete_custom_role(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, 'admin', 'adminpass123')

        create_response = client.post(
            '/api/roles',
            json={
                'name': 'Operations',
                'identifier': 'operations',
                'description': 'Operations role',
                'permissions': {
                    'skills': {'module_permissions': {'view': True, 'enable_disable': False}, 'skill_permissions': []},
                    'channels': {
                        'module_permissions': {'manage_permissions': False},
                        'channel_permissions': [
                            {
                                'channel_type': 'websocket',
                                'channel_name': 'WebSocket',
                                'allowed': True,
                            },
                        ],
                    },
                    'tokens': {'view': False, 'create': False, 'edit': False, 'delete': False},
                    'users': {'view': True, 'create': False, 'edit': False, 'delete': False},
                    'roles': {'view': False, 'create': False, 'edit': False, 'delete': False},
                },
                'is_active': True,
            },
            headers={'AtlasClaw-Authenticate': token},
        )

        assert create_response.status_code == 201
        role_id = create_response.json()['id']

        update_response = client.put(
            f'/api/roles/{role_id}',
            json={'description': 'Updated operations role', 'is_active': False},
            headers={'AtlasClaw-Authenticate': token},
        )

        assert update_response.status_code == 200
        assert update_response.json()['description'] == 'Updated operations role'
        assert update_response.json()['is_active'] is False

        delete_response = client.delete(f'/api/roles/{role_id}', headers={'AtlasClaw-Authenticate': token})
        assert delete_response.status_code == 204

        get_response = client.get(f'/api/roles/{role_id}', headers={'AtlasClaw-Authenticate': token})
        assert get_response.status_code == 404

        _cleanup_manager(manager)

    def test_duplicate_role_identifier_returns_409(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, 'admin', 'adminpass123')

        client.post(
            '/api/roles',
            json={'name': 'Operations', 'identifier': 'operations', 'permissions': {}, 'is_active': True},
            headers={'AtlasClaw-Authenticate': token},
        )
        duplicate_response = client.post(
            '/api/roles',
            json={'name': 'Operations 2', 'identifier': 'operations', 'permissions': {}, 'is_active': True},
            headers={'AtlasClaw-Authenticate': token},
        )

        assert duplicate_response.status_code == 409

        _cleanup_manager(manager)

    def test_role_identifier_cannot_be_changed_after_creation(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, 'admin', 'adminpass123')

        create_response = client.post(
            '/api/roles',
            json={
                'name': 'Operations',
                'identifier': 'operations',
                'description': 'Operations role',
                'permissions': {},
                'is_active': True,
            },
            headers={'AtlasClaw-Authenticate': token},
        )
        assert create_response.status_code == 201
        role_id = create_response.json()['id']

        update_response = client.put(
            f'/api/roles/{role_id}',
            json={'identifier': 'ops-renamed'},
            headers={'AtlasClaw-Authenticate': token},
        )

        assert update_response.status_code == 400
        assert 'cannot be changed' in update_response.json()['detail'].lower()

        get_response = client.get(f'/api/roles/{role_id}', headers={'AtlasClaw-Authenticate': token})
        assert get_response.status_code == 200
        assert get_response.json()['identifier'] == 'operations'

        _cleanup_manager(manager)

    def test_non_admin_cannot_manage_roles(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, 'regularuser', 'userpass123')

        response = client.get('/api/roles', headers={'AtlasClaw-Authenticate': token})
        assert response.status_code == 403

        _cleanup_manager(manager)

    def test_module_governor_cannot_access_role_catalog_without_role_permissions(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        admin_token = _login_as(client, 'admin', 'adminpass123')

        create_response = client.post(
            '/api/roles',
            json={
                'name': 'Skill Governor',
                'identifier': 'skill-governor',
                'description': 'Can govern skill permissions only',
                'permissions': {
                    'skills': {
                        'module_permissions': {
                            'manage_permissions': True,
                        },
                        'skill_permissions': [],
                    },
                },
                'is_active': True,
            },
            headers={'AtlasClaw-Authenticate': admin_token},
        )
        assert create_response.status_code == 201

        async def _assign_role():
            async with _test_db_manager.get_session() as session:
                user = await UserService.get_by_username(session, 'regularuser')
                await UserService.update(
                    session,
                    user.id,
                    UserUpdate(roles={'skill-governor': True}),
                )

        asyncio.run(_assign_role())
        regular_token = _login_as(client, 'regularuser', 'userpass123')

        response = client.get('/api/roles', headers={'AtlasClaw-Authenticate': regular_token})
        assert response.status_code == 403

        _cleanup_manager(manager)

    def test_skill_execution_requires_runtime_skill_access(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        admin_token = _login_as(client, 'admin', 'adminpass123')

        governance_role = client.post(
            '/api/roles',
            json={
                'name': 'Skill Governor',
                'identifier': 'skill-governor',
                'description': 'Can govern skill permissions only',
                'permissions': {
                    'skills': {
                        'module_permissions': {
                            'view': True,
                            'manage_permissions': True,
                        },
                        'skill_permissions': [],
                    },
                },
                'is_active': True,
            },
            headers={'AtlasClaw-Authenticate': admin_token},
        )
        assert governance_role.status_code == 201

        async def _assign_governance_role():
            async with _test_db_manager.get_session() as session:
                user = await UserService.get_by_username(session, 'regularuser')
                await UserService.update(
                    session,
                    user.id,
                    UserUpdate(roles={'skill-governor': True}),
                )

        asyncio.run(_assign_governance_role())
        regular_token = _login_as(client, 'regularuser', 'userpass123')

        list_response = client.get('/api/skills', headers={'AtlasClaw-Authenticate': regular_token})
        assert list_response.status_code == 200

        denied_execute = client.post(
            '/api/skills/execute',
            json={'skill_name': 'echo-skill', 'args': {'message': 'hello'}},
            headers={'AtlasClaw-Authenticate': regular_token},
        )
        assert denied_execute.status_code == 403

        runner_role = client.post(
            '/api/roles',
            json={
                'name': 'Skill Runner',
                'identifier': 'skill-runner',
                'description': 'Can execute one skill',
                'permissions': {
                    'skills': {
                        'module_permissions': {
                            'view': False,
                        },
                        'skill_permissions': [
                            {
                                'skill_id': 'echo-skill',
                                'skill_name': 'echo-skill',
                                'description': 'Echo test skill',
                                'authorized': True,
                                'enabled': True,
                            },
                        ],
                    },
                },
                'is_active': True,
            },
            headers={'AtlasClaw-Authenticate': admin_token},
        )
        assert runner_role.status_code == 201

        async def _assign_runtime_role():
            async with _test_db_manager.get_session() as session:
                user = await UserService.get_by_username(session, 'regularuser')
                await UserService.update(
                    session,
                    user.id,
                    UserUpdate(roles={'skill-runner': True}),
                )

        asyncio.run(_assign_runtime_role())
        runtime_token = _login_as(client, 'regularuser', 'userpass123')

        allowed_execute = client.post(
            '/api/skills/execute',
            json={'skill_name': 'echo-skill', 'args': {'message': 'hello'}},
            headers={'AtlasClaw-Authenticate': runtime_token},
        )
        assert allowed_execute.status_code == 200
        assert json.loads(allowed_execute.json()['result']) == {'echo': 'hello'}

        _cleanup_manager(manager)

    def test_builtin_user_skill_permissions_can_be_modified_via_api(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, 'admin', 'adminpass123')
        headers = {'AtlasClaw-Authenticate': token}

        roles_response = client.get('/api/roles', headers=headers)
        assert roles_response.status_code == 200
        user_role = next(role for role in roles_response.json()['roles'] if role['identifier'] == 'user')

        update_response = client.put(
            f"/api/roles/{user_role['id']}",
            json={
                'permissions': {
                    'skills': {
                        'module_permissions': {
                            'view': True,
                        },
                        'skill_permissions': [
                            {
                                'skill_id': 'echo-skill',
                                'skill_name': 'echo-skill',
                                'description': 'Echo test skill',
                                'authorized': True,
                                'enabled': True,
                            },
                        ],
                    },
                },
            },
            headers=headers,
        )

        assert update_response.status_code == 200
        payload = update_response.json()
        assert payload['permissions']['channels']['module_permissions']['manage_permissions'] is False
        payload_channel_types = {
            entry['channel_type']
            for entry in payload['permissions']['channels']['channel_permissions']
            if entry['allowed'] is True
        }
        assert payload_channel_types
        assert payload['permissions']['skills']['module_permissions']['view'] is True
        assert payload['permissions']['skills']['skill_permissions'] == [
            {
                'skill_id': 'echo-skill',
                'skill_name': 'echo-skill',
                'description': 'Echo test skill',
                'authorized': True,
                'enabled': True,
            },
        ]

        refreshed_roles_response = client.get('/api/roles', headers=headers)
        refreshed_user_role = next(
            role for role in refreshed_roles_response.json()['roles'] if role['identifier'] == 'user'
        )
        assert refreshed_user_role['permissions']['skills']['skill_permissions'] == [
            {
                'skill_id': 'echo-skill',
                'skill_name': 'echo-skill',
                'description': 'Echo test skill',
                'authorized': True,
                'enabled': True,
            },
        ]

        _cleanup_manager(manager)

    def test_role_update_accepts_tool_group_and_stores_concrete_permissions(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        try:
            client = _build_client(tmp_path, _get_auth_config(), with_builtin_tools=True)
            token = _login_as(client, 'admin', 'adminpass123')
            headers = {'AtlasClaw-Authenticate': token}

            roles_response = client.get('/api/roles', headers=headers)
            assert roles_response.status_code == 200
            user_role = next(role for role in roles_response.json()['roles'] if role['identifier'] == 'user')

            update_response = client.put(
                f"/api/roles/{user_role['id']}",
                json={
                    'permissions': {
                        'skills': {
                            'module_permissions': {
                                'view': True,
                            },
                            'skill_permissions': [
                                {
                                    'skill_id': 'group:fs',
                                    'skill_name': 'group:fs',
                                    'description': 'Filesystem tools',
                                    'authorized': True,
                                    'enabled': True,
                                },
                            ],
                        },
                    },
                },
                headers=headers,
            )

            assert update_response.status_code == 200
            response_entries = update_response.json()['permissions']['skills']['skill_permissions']
            response_by_id = {entry['skill_id']: entry for entry in response_entries}
            assert 'group:fs' in response_by_id
            assert response_by_id['group:fs']['type'] == 'tool_group'
            assert response_by_id['group:fs']['member_skill_ids'] == ['read', 'write', 'edit', 'delete']
            for concrete_tool in ('read', 'write', 'edit', 'delete'):
                assert concrete_tool not in response_by_id

            async def _stored_skill_ids() -> set[str]:
                async with _test_db_manager.get_session() as session:
                    stored_user_role = await RoleService.get_by_identifier(session, 'user')
                    return {
                        entry['skill_id']
                        for entry in stored_user_role.permissions['skills']['skill_permissions']
                    }

            stored_skill_ids = asyncio.run(_stored_skill_ids())
            assert {'read', 'write', 'edit', 'delete'}.issubset(stored_skill_ids)
            assert 'group:fs' not in stored_skill_ids

            refreshed_roles_response = client.get('/api/roles', headers=headers)
            assert refreshed_roles_response.status_code == 200
            refreshed_user_role = next(
                role for role in refreshed_roles_response.json()['roles'] if role['identifier'] == 'user'
            )
            refreshed_by_id = {
                entry['skill_id']
                for entry in refreshed_user_role['permissions']['skills']['skill_permissions']
            }
            assert 'group:fs' in refreshed_by_id
            assert {'read', 'write', 'edit', 'delete'}.isdisjoint(refreshed_by_id)
        finally:
            _cleanup_manager(manager)

    def test_builtin_user_provider_permissions_can_be_modified_via_api(self, tmp_path):
        manager = _init_database_sync(tmp_path)

        async def _seed_provider():
            async with _test_db_manager.get_session() as session:
                await ServiceProviderConfigService.create(
                    session,
                    ServiceProviderConfigCreate(
                        provider_type='smartcmp',
                        instance_name='default',
                        config={
                            'base_url': 'https://cmp.example.com',
                            'auth_type': 'user_token',
                        },
                        is_active=True,
                    ),
                )

        asyncio.run(_seed_provider())
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, 'admin', 'adminpass123')
        headers = {'AtlasClaw-Authenticate': token}

        roles_response = client.get('/api/roles', headers=headers)
        assert roles_response.status_code == 200
        user_role = next(role for role in roles_response.json()['roles'] if role['identifier'] == 'user')

        skill_update_response = client.put(
            f"/api/roles/{user_role['id']}",
            json={
                'permissions': {
                    'skills': {
                        'module_permissions': {
                            'view': True,
                        },
                        'skill_permissions': [
                            {
                                'skill_id': 'echo-skill',
                                'skill_name': 'echo-skill',
                                'description': 'Echo test skill',
                                'authorized': True,
                                'enabled': True,
                            },
                        ],
                    },
                },
            },
            headers=headers,
        )
        assert skill_update_response.status_code == 200

        update_response = client.put(
            f"/api/roles/{user_role['id']}",
            json={
                'permissions': {
                    'providers': {
                        'module_permissions': {
                            'manage_permissions': False,
                        },
                        'provider_permissions': [
                            {
                                'provider_type': 'smartcmp',
                                'instance_name': 'default',
                                'allowed': False,
                            },
                        ],
                    },
                },
            },
            headers=headers,
        )

        assert update_response.status_code == 200
        assert update_response.json()['permissions']['providers']['provider_permissions'] == [
            {
                'provider_type': 'smartcmp',
                'instance_name': 'default',
                'allowed': False,
            },
        ]
        assert update_response.json()['permissions']['channels']['channel_permissions']
        assert update_response.json()['permissions']['skills']['skill_permissions'] == [
            {
                'skill_id': 'echo-skill',
                'skill_name': 'echo-skill',
                'description': 'Echo test skill',
                'authorized': True,
                'enabled': True,
            },
        ]

        refreshed_roles_response = client.get('/api/roles', headers=headers)
        refreshed_user_role = next(
            role for role in refreshed_roles_response.json()['roles'] if role['identifier'] == 'user'
        )
        assert refreshed_user_role['permissions']['providers']['provider_permissions'] == [
            {
                'provider_type': 'smartcmp',
                'instance_name': 'default',
                'allowed': False,
            },
        ]
        assert refreshed_user_role['permissions']['skills']['skill_permissions'] == [
            {
                'skill_id': 'echo-skill',
                'skill_name': 'echo-skill',
                'description': 'Echo test skill',
                'authorized': True,
                'enabled': True,
            },
        ]

        _cleanup_manager(manager)

    def test_provider_permissions_require_explicit_allow_and_multi_role_allow_priority(self, tmp_path):
        manager = _init_database_sync(tmp_path)

        async def _seed_roles_and_resolve():
            async with _test_db_manager.get_session() as session:
                await RoleService.ensure_builtin_roles(session)
                deny_role = await RoleService.create(
                    session,
                    RoleCreate(
                        name='Provider Deny',
                        identifier='provider_deny',
                        description='Denies one provider instance.',
                        permissions={
                            'providers': {
                                'provider_permissions': [
                                    {
                                        'provider_type': 'smartcmp',
                                        'instance_name': 'default',
                                        'allowed': False,
                                    },
                                ],
                            },
                        },
                        is_active=True,
                    ),
                )
                allow_role = await RoleService.create(
                    session,
                    RoleCreate(
                        name='Provider Allow',
                        identifier='provider_allow',
                        description='Allows one provider instance.',
                        permissions={
                            'providers': {
                                'provider_permissions': [
                                    {
                                        'provider_type': 'smartcmp',
                                        'instance_name': 'default',
                                        'allowed': True,
                                    },
                                ],
                            },
                        },
                        is_active=True,
                    ),
                )
                user = await UserService.get_by_username(session, 'regularuser')
                await UserService.update(
                    session,
                    user.id,
                    UserUpdate(roles={deny_role.identifier: True, allow_role.identifier: True}),
                )
                authz = await resolve_authorization_context(
                    session,
                    UserInfo(
                        user_id='regularuser',
                        display_name='Regular User',
                        roles=[],
                        auth_type='local',
                    ),
                )
                allowed_permissions = authz.permissions['providers']['provider_permissions']

                await UserService.update(
                    session,
                    user.id,
                    UserUpdate(roles={deny_role.identifier: True}),
                )
                denied_authz = await resolve_authorization_context(
                    session,
                    UserInfo(
                        user_id='regularuser',
                        display_name='Regular User',
                        roles=[],
                        auth_type='local',
                    ),
                )
                denied_permissions = denied_authz.permissions['providers']['provider_permissions']
                return allowed_permissions, denied_permissions

        allowed_permissions, denied_permissions = asyncio.run(_seed_roles_and_resolve())
        assert allowed_permissions == [
            {
                'provider_type': 'smartcmp',
                'instance_name': 'default',
                'allowed': True,
            },
        ]
        assert denied_permissions == [
            {
                'provider_type': 'smartcmp',
                'instance_name': 'default',
                'allowed': False,
            },
        ]

        _cleanup_manager(manager)

    def test_provider_catalog_and_settings_respect_provider_permissions(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        admin_token = _login_as(client, 'admin', 'adminpass123')
        admin_headers = {'AtlasClaw-Authenticate': admin_token}

        async def _seed_provider_and_deny_role():
            async with _test_db_manager.get_session() as session:
                await ServiceProviderConfigService.create(
                    session,
                    ServiceProviderConfigCreate(
                        provider_type='smartcmp',
                        instance_name='default',
                        config={
                            'base_url': 'https://cmp.example.com',
                            'auth_type': 'user_token',
                        },
                        is_active=True,
                    ),
                )
                deny_role = await RoleService.create(
                    session,
                    RoleCreate(
                        name='Provider Deny Runtime',
                        identifier='provider_deny_runtime',
                        description='Cannot use smartcmp default.',
                        permissions={
                            'providers': {
                                'provider_permissions': [
                                    {
                                        'provider_type': 'smartcmp',
                                        'instance_name': 'default',
                                        'allowed': False,
                                    },
                                ],
                            },
                        },
                        is_active=True,
                    ),
                )
                user = await UserService.get_by_username(session, 'regularuser')
                await UserService.update(
                    session,
                    user.id,
                    UserUpdate(roles={deny_role.identifier: True}),
                )

        asyncio.run(_seed_provider_and_deny_role())

        regular_token = _login_as(client, 'regularuser', 'userpass123')
        regular_headers = {'AtlasClaw-Authenticate': regular_token}
        denied_catalog_resp = client.get('/api/service-providers/available-instances', headers=regular_headers)
        assert denied_catalog_resp.status_code == 200
        assert denied_catalog_resp.json()['providers'] == []

        denied_settings_resp = client.put(
            '/api/users/me/provider-settings',
            json={
                'provider_type': 'smartcmp',
                'instance_name': 'default',
                'config': {'user_token': 'user-token'},
            },
            headers=regular_headers,
        )
        assert denied_settings_resp.status_code == 403

        full_catalog_resp = client.get(
            '/api/service-providers/available-instances?include_all=true',
            headers=admin_headers,
        )
        assert full_catalog_resp.status_code == 200
        assert full_catalog_resp.json()['providers'][0]['provider_type'] == 'smartcmp'

        async def _add_empty_provider_role():
            async with _test_db_manager.get_session() as session:
                empty_role = await RoleService.create(
                    session,
                    RoleCreate(
                        name='Provider Runtime Empty',
                        identifier='provider_runtime_empty',
                        description='Omits provider access rules.',
                        permissions={},
                        is_active=True,
                    ),
                )
                user = await UserService.get_by_username(session, 'regularuser')
                await UserService.update(
                    session,
                    user.id,
                    UserUpdate(
                        roles={
                            'provider_deny_runtime': True,
                            empty_role.identifier: True,
                        },
                    ),
                )

        asyncio.run(_add_empty_provider_role())
        regular_token = _login_as(client, 'regularuser', 'userpass123')
        regular_headers = {'AtlasClaw-Authenticate': regular_token}
        still_denied_catalog_resp = client.get('/api/service-providers/available-instances', headers=regular_headers)
        assert still_denied_catalog_resp.status_code == 200
        assert still_denied_catalog_resp.json()['providers'] == []

        async def _add_explicit_allow_role():
            async with _test_db_manager.get_session() as session:
                allow_role = await RoleService.create(
                    session,
                    RoleCreate(
                        name='Provider Runtime Allow',
                        identifier='provider_runtime_allow',
                        description='Allows smartcmp default.',
                        permissions={
                            'providers': {
                                'provider_permissions': [
                                    {
                                        'provider_type': 'smartcmp',
                                        'instance_name': 'default',
                                        'allowed': True,
                                    },
                                ],
                            },
                        },
                        is_active=True,
                    ),
                )
                user = await UserService.get_by_username(session, 'regularuser')
                await UserService.update(
                    session,
                    user.id,
                    UserUpdate(
                        roles={
                            'provider_deny_runtime': True,
                            'provider_runtime_empty': True,
                            allow_role.identifier: True,
                        },
                    ),
                )

        asyncio.run(_add_explicit_allow_role())
        regular_token = _login_as(client, 'regularuser', 'userpass123')
        regular_headers = {'AtlasClaw-Authenticate': regular_token}
        allowed_catalog_resp = client.get('/api/service-providers/available-instances', headers=regular_headers)
        assert allowed_catalog_resp.status_code == 200
        assert allowed_catalog_resp.json()['providers'][0]['provider_type'] == 'smartcmp'

        _cleanup_manager(manager)

    def test_builtin_role_cannot_be_deleted(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, 'admin', 'adminpass123')

        roles_response = client.get('/api/roles', headers={'AtlasClaw-Authenticate': token})
        admin_role = next(role for role in roles_response.json()['roles'] if role['identifier'] == 'admin')

        delete_response = client.delete(
            f"/api/roles/{admin_role['id']}",
            headers={'AtlasClaw-Authenticate': token},
        )

        assert delete_response.status_code == 400
        assert 'built-in' in delete_response.json()['detail'].lower()

        _cleanup_manager(manager)

    def test_builtin_role_metadata_cannot_be_modified(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, 'admin', 'adminpass123')

        roles_response = client.get('/api/roles', headers={'AtlasClaw-Authenticate': token})
        admin_role = next(role for role in roles_response.json()['roles'] if role['identifier'] == 'admin')

        update_response = client.put(
            f"/api/roles/{admin_role['id']}",
            json={'is_active': False},
            headers={'AtlasClaw-Authenticate': token},
        )

        assert update_response.status_code == 400
        assert 'metadata is read-only' in update_response.json()['detail'].lower()

        _cleanup_manager(manager)

    def test_non_admin_builtin_role_permissions_can_be_modified_and_persisted(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, 'admin', 'adminpass123')

        roles_response = client.get('/api/roles', headers={'AtlasClaw-Authenticate': token})
        viewer_role = next(role for role in roles_response.json()['roles'] if role['identifier'] == 'viewer')

        update_response = client.put(
            f"/api/roles/{viewer_role['id']}",
            json={
                'permissions': {
                    'channels': {
                        'module_permissions': {
                            'manage_permissions': True,
                        },
                        'channel_permissions': [
                            {
                                'channel_type': 'websocket',
                                'channel_name': 'WebSocket',
                                'allowed': False,
                            },
                        ],
                    },
                },
            },
            headers={'AtlasClaw-Authenticate': token},
        )

        assert update_response.status_code == 200
        assert update_response.json()['permissions']['channels']['module_permissions']['manage_permissions'] is True
        assert update_response.json()['permissions']['channels']['channel_permissions'] == [
            {
                'channel_type': 'websocket',
                'channel_name': 'WebSocket',
                'allowed': False,
            },
        ]

        refreshed_roles_response = client.get('/api/roles', headers={'AtlasClaw-Authenticate': token})
        refreshed_viewer_role = next(
            role for role in refreshed_roles_response.json()['roles'] if role['identifier'] == 'viewer'
        )
        assert refreshed_viewer_role['permissions']['channels']['module_permissions']['manage_permissions'] is True
        refreshed_channels_by_type = {
            entry['channel_type']: entry
            for entry in refreshed_viewer_role['permissions']['channels']['channel_permissions']
        }
        assert refreshed_channels_by_type['websocket']['allowed'] is False

        _cleanup_manager(manager)

    def test_builtin_admin_locked_permissions_are_repaired_during_builtin_sync(self, tmp_path):
        manager = _init_database_sync(tmp_path)

        async def _seed_restricted_admin_permissions():
            async with _test_db_manager.get_session() as session:
                await ServiceProviderConfigService.create(
                    session,
                    ServiceProviderConfigCreate(
                        provider_type='smartcmp',
                        instance_name='default',
                        config={
                            'base_url': 'https://cmp.example.com',
                            'auth_type': 'user_token',
                        },
                        is_active=True,
                    ),
                )
                await RoleService.ensure_builtin_roles(session)
                admin_role = await RoleService.get_by_identifier(session, 'admin')
                admin_role.permissions = {
                    'skills': {
                        'module_permissions': {
                            'view': True,
                            'enable_disable': True,
                            'manage_permissions': True,
                        },
                        'skill_permissions': [
                            {
                                'skill_id': 'group:runtime',
                                'skill_name': 'group:runtime',
                                'authorized': False,
                                'enabled': False,
                            },
                        ],
                    },
                    'providers': {
                        'module_permissions': {
                            'manage_permissions': True,
                        },
                        'provider_permissions': [],
                    },
                    'roles': {
                        'view': False,
                        'create': False,
                        'edit': False,
                        'delete': False,
                    },
                    'tokens': {
                        'view': False,
                        'create': False,
                        'edit': False,
                        'delete': False,
                        'manage_permissions': False,
                    },
                }
                await session.flush()

        asyncio.run(_seed_restricted_admin_permissions())

        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, 'admin', 'adminpass123')
        headers = {'AtlasClaw-Authenticate': token}

        roles_response = client.get('/api/roles', headers=headers)
        assert roles_response.status_code == 200
        admin_role = next(role for role in roles_response.json()['roles'] if role['identifier'] == 'admin')
        assert admin_role['permissions']['roles']['view'] is True
        assert admin_role['permissions']['tokens']['view'] is True
        assert admin_role['permissions']['tokens']['create'] is True
        admin_skill_entries = admin_role['permissions']['skills']['skill_permissions']
        assert len(admin_skill_entries) == 1
        assert admin_skill_entries[0]['skill_id'] == 'group:runtime'
        assert admin_skill_entries[0]['authorized'] is False
        assert admin_skill_entries[0]['enabled'] is False
        admin_provider_keys = {
            (entry['provider_type'], entry['instance_name'])
            for entry in admin_role['permissions']['providers']['provider_permissions']
            if entry['allowed'] is True
        }
        assert admin_provider_keys == set()

        token_config_response = client.get('/api/token-configs?page=1&page_size=20', headers=headers)
        assert token_config_response.status_code == 200

        _cleanup_manager(manager)

    def test_builtin_admin_permissions_cannot_be_modified_via_api(self, tmp_path):
        """Non-skills modules on system-managed roles are silently preserved.

        The frontend sends a full permissions shape.  The backend accepts it
        (200) but force-restores non-skills modules from the stored record,
        so the effective tokens.view value remains True even though the
        client sent False.
        """
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, 'admin', 'adminpass123')
        headers = {'AtlasClaw-Authenticate': token}

        roles_response = client.get('/api/roles', headers=headers)
        admin_role = next(role for role in roles_response.json()['roles'] if role['identifier'] == 'admin')
        original_tokens_view = admin_role['permissions']['tokens']['view']

        update_response = client.put(
            f"/api/roles/{admin_role['id']}",
            json={
                'permissions': {
                    'tokens': {
                        'view': False,
                    },
                },
            },
            headers=headers,
        )

        # Accepted, but non-skills modules are silently restored.
        assert update_response.status_code == 200
        assert update_response.json()['permissions']['tokens']['view'] == original_tokens_view

        _cleanup_manager(manager)

    def test_assigned_role_cannot_be_deleted(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, 'admin', 'adminpass123')

        create_response = client.post(
            '/api/roles',
            json={'name': 'Support', 'identifier': 'support', 'permissions': {}, 'is_active': True},
            headers={'AtlasClaw-Authenticate': token},
        )
        assert create_response.status_code == 201
        role_id = create_response.json()['id']

        users_response = client.get('/api/users?search=regularuser', headers={'AtlasClaw-Authenticate': token})
        user_id = users_response.json()['users'][0]['id']

        assign_response = client.put(
            f'/api/users/{user_id}',
            json={'roles': {'support': True}},
            headers={'AtlasClaw-Authenticate': token},
        )
        assert assign_response.status_code == 200

        delete_response = client.delete(f'/api/roles/{role_id}', headers={'AtlasClaw-Authenticate': token})
        assert delete_response.status_code == 400
        assert 'assigned' in delete_response.json()['detail'].lower()

        _cleanup_manager(manager)

    def test_assigned_role_delete_supports_legacy_list_storage(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        token = _login_as(client, 'admin', 'adminpass123')

        create_response = client.post(
            '/api/roles',
            json={'name': 'Support', 'identifier': 'support', 'permissions': {}, 'is_active': True},
            headers={'AtlasClaw-Authenticate': token},
        )
        assert create_response.status_code == 201
        role_id = create_response.json()['id']

        async def _seed_legacy_role_storage():
            async with _test_db_manager.get_session() as session:
                user = await UserService.get_by_username(session, 'regularuser')
                user.roles = ['support']
                await session.flush()

        asyncio.run(_seed_legacy_role_storage())

        delete_response = client.delete(f'/api/roles/{role_id}', headers={'AtlasClaw-Authenticate': token})
        assert delete_response.status_code == 400
        assert 'assigned' in delete_response.json()['detail'].lower()

        _cleanup_manager(manager)

    def test_module_permission_governor_can_only_edit_managed_modules(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        admin_token = _login_as(client, 'admin', 'adminpass123')
        admin_headers = {'AtlasClaw-Authenticate': admin_token}

        create_manager_resp = client.post(
            '/api/users',
            json={
                'username': 'rolemanager',
                'password': 'rolemanagerpass123',
                'display_name': 'Role Manager',
                'email': 'rolemanager@test.com',
                'roles': {},
                'is_active': True,
            },
            headers=admin_headers,
        )
        assert create_manager_resp.status_code == 201
        manager_user_id = create_manager_resp.json()['id']

        target_role_resp = client.post(
            '/api/roles',
            json={
                'name': 'Target Role',
                'identifier': 'target_role',
                'description': 'Role to be governed',
                'permissions': {},
                'is_active': True,
            },
            headers=admin_headers,
        )
        assert target_role_resp.status_code == 201
        target_role_id = target_role_resp.json()['id']

        governor_role_resp = client.post(
            '/api/roles',
            json={
                'name': 'User Permission Governor',
                'identifier': 'user_permission_governor',
                'description': 'Can govern user permission policies only.',
                'permissions': {
                    'users': {
                        'manage_permissions': True,
                    },
                    'roles': {
                        'view': True,
                    },
                },
                'is_active': True,
            },
            headers=admin_headers,
        )
        assert governor_role_resp.status_code == 201

        assign_governor_resp = client.put(
            f'/api/users/{manager_user_id}',
            json={'roles': {'user_permission_governor': True}},
            headers=admin_headers,
        )
        assert assign_governor_resp.status_code == 200

        manager_token = _login_as(client, 'rolemanager', 'rolemanagerpass123')
        manager_headers = {'AtlasClaw-Authenticate': manager_token}

        list_roles_resp = client.get('/api/roles?page=1&page_size=20', headers=manager_headers)
        assert list_roles_resp.status_code == 200

        manage_users_resp = client.put(
            f'/api/roles/{target_role_id}',
            json={
                'permissions': {
                    'users': {
                        'view': True,
                        'manage_permissions': True,
                    },
                },
            },
            headers=manager_headers,
        )
        assert manage_users_resp.status_code == 200
        assert manage_users_resp.json()['permissions']['users']['view'] is True
        assert manage_users_resp.json()['permissions']['users']['manage_permissions'] is True

        manage_skills_resp = client.put(
            f'/api/roles/{target_role_id}',
            json={
                'permissions': {
                    'skills': {
                        'module_permissions': {
                            'view': True,
                        },
                    },
                },
            },
            headers=manager_headers,
        )
        assert manage_skills_resp.status_code == 403
        assert 'skills' in manage_skills_resp.json()['detail'].lower()

        rename_role_resp = client.put(
            f'/api/roles/{target_role_id}',
            json={'description': 'Renamed by governor'},
            headers=manager_headers,
        )
        assert rename_role_resp.status_code == 403
        assert 'roles.edit' in rename_role_resp.json()['detail'].lower()

        _cleanup_manager(manager)

    def test_user_with_skill_view_can_list_skills(self, tmp_path):
        manager = _init_database_sync(tmp_path)
        client = _build_client(tmp_path, _get_auth_config())
        admin_token = _login_as(client, 'admin', 'adminpass123')
        admin_headers = {'AtlasClaw-Authenticate': admin_token}

        create_user_resp = client.post(
            '/api/users',
            json={
                'username': 'skillviewer',
                'password': 'skillviewerpass123',
                'display_name': 'Skill Viewer',
                'email': 'skillviewer@test.com',
                'roles': {},
                'is_active': True,
            },
            headers=admin_headers,
        )
        assert create_user_resp.status_code == 201
        skill_viewer_id = create_user_resp.json()['id']

        create_role_resp = client.post(
            '/api/roles',
            json={
                'name': 'Skill Viewer',
                'identifier': 'skill_viewer',
                'description': 'Can browse skill catalog.',
                'permissions': {
                    'skills': {
                        'module_permissions': {
                            'view': True,
                        },
                    },
                },
                'is_active': True,
            },
            headers=admin_headers,
        )
        assert create_role_resp.status_code == 201

        assign_role_resp = client.put(
            f"/api/users/{skill_viewer_id}",
            json={'roles': {'skill_viewer': True}},
            headers=admin_headers,
        )
        assert assign_role_resp.status_code == 200

        viewer_token = _login_as(client, 'skillviewer', 'skillviewerpass123')
        viewer_headers = {'AtlasClaw-Authenticate': viewer_token}

        skills_resp = client.get('/api/skills', headers=viewer_headers)
        assert skills_resp.status_code == 200
        assert isinstance(skills_resp.json()['skills'], list)

        regular_token = _login_as(client, 'regularuser', 'userpass123')
        regular_resp = client.get('/api/skills', headers={'AtlasClaw-Authenticate': regular_token})
        assert regular_resp.status_code == 403

        _cleanup_manager(manager)
