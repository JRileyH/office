"""Deleting a project: everything office made for it but the GitHub repo, which office's
token can't delete. Used by !delete and by the nuke."""

from __future__ import annotations

import asyncio
import shutil

from .config import Settings
from .deployer_client import Deployer
from .matrix import Chat
from .store import Project, Store
from .workspace import Workspace


async def delete_project(
    project: Project, settings: Settings, store: Store, chat: Chat, workspace: Workspace, deployer: Deployer
) -> None:
    """Stop the project's jobs first. If a step fails (DeployerError, WorkspaceError), the steps
    after it haven't happened and the project is still listed, so running this again finishes."""
    await deployer.delete(project.slug)  # the site, its data and its route
    await workspace.remove(project.slug)
    for job_id in store.delete_project(project.slug):
        await asyncio.to_thread(shutil.rmtree, settings.job_logs(job_id), ignore_errors=True)
    if project.room_id:
        await chat.close_room(project.room_id, f"{project.name} was deleted.")
