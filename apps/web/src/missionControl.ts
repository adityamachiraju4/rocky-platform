// Composed read for Mission Control. Frontend-only: fans out listTasks per
// project (finding #8 — bounded N+1, no backend aggregate endpoint in v1).
//
// Exposed as a single stable module-level fetcher so useResource's
// [state.nonce, fetcher] effect does not loop on re-render.

import { listActivity, listProjects, listTasks } from "./api";
import type { Activity, Project, Task } from "./types";

export interface ProjectSummary {
  project: Project;
  activeTaskCount: number;
}

export interface ActiveTaskRef {
  task: Task;
  projectId: string;
  projectName: string;
}

export interface EntityRef {
  kind: "project" | "task";
  id: string;
  name: string;
  projectId: string;
  projectName: string;
}
export interface MissionControlData {
  projects: ProjectSummary[];
  activeTasks: ActiveTaskRef[];
  recentActivity: Activity[];
  latestActivity: Activity | null;
  entityById: Map<string, EntityRef>;
}

const RECENT_ACTIVITY_LIMIT = 8;

function isActive(task: Task): boolean {
  return task.status === "active";
}

export async function loadMissionControl(): Promise<MissionControlData> {
  const projects = await listProjects();

  // One listTasks call per project, in parallel. Bounded by project count.
  const taskLists = await Promise.all(
    projects.map((p) => listTasks(p.id)),
  );

  const projectSummaries: ProjectSummary[] = projects.map((project, i) => ({
    project,
    activeTaskCount: taskLists[i].filter(isActive).length,
  }));

  const activeTasks: ActiveTaskRef[] = [];
  projects.forEach((project, i) => {
    for (const task of taskLists[i]) {
      if (isActive(task)) {
        activeTasks.push({
          task,
          projectId: project.id,
          projectName: project.name,
        });
      }
    }
  });

  const entityById = new Map<string, EntityRef>();
  projects.forEach((project, i) => {
    entityById.set(project.id, {
      kind: "project",
      id: project.id,
      name: project.name,
      projectId: project.id,
      projectName: project.name,
    });
    for (const task of taskLists[i]) {
      entityById.set(task.id, {
        kind: "task",
        id: task.id,
        name: task.title,
        projectId: project.id,
        projectName: project.name,
      });
    }
  });
  const activity = await listActivity();
  const recentActivity = activity.slice(0, RECENT_ACTIVITY_LIMIT);
  const latestActivity = activity.length > 0 ? activity[0] : null;

  return {
    projects: projectSummaries,
    activeTasks,
    recentActivity,
    latestActivity,
    entityById,
  };
}
