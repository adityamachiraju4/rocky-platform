import {
  getAuthenticatedProfile,
  listActivity,
  listNotifications,
  listProjects,
  listReminders,
  listTasks,
} from "./api";
import { AuthExpiredError } from "./api";
import type {
  Activity,
  Notification,
  Project,
  Reminder,
  Task,
  UserProfile,
} from "./types";

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

type Panel = "work" | "activity" | "reminders" | "notifications" | "profile";

export interface MissionControlData {
  projects: ProjectSummary[];
  activeTasks: ActiveTaskRef[];
  recentActivity: Activity[];
  reminders: Reminder[];
  notifications: Notification[];
  profile: UserProfile | null;
  entityById: Map<string, EntityRef>;
  errors: Partial<Record<Panel, string>>;
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "This section is unavailable.";
}

export async function loadMissionControl(): Promise<MissionControlData> {
  const results = await Promise.allSettled([
    listProjects(),
    listActivity(),
    listReminders(),
    listNotifications(),
    getAuthenticatedProfile(),
  ] as const);
  const [projectsResult, activityResult, remindersResult, notificationsResult, profileResult] = results;

  const authFailure = results.find(
    (result) => result.status === "rejected" && result.reason instanceof AuthExpiredError,
  );
  if (authFailure?.status === "rejected") throw authFailure.reason;

  const errors: MissionControlData["errors"] = {};
  const projects = projectsResult.status === "fulfilled" ? projectsResult.value : [];
  if (projectsResult.status === "rejected") errors.work = errorMessage(projectsResult.reason);

  const taskResults = await Promise.allSettled(projects.map((project) => listTasks(project.id)));
  const taskAuthFailure = taskResults.find(
    (result) => result.status === "rejected" && result.reason instanceof AuthExpiredError,
  );
  if (taskAuthFailure?.status === "rejected") throw taskAuthFailure.reason;
  const taskLists = taskResults.map((result) => result.status === "fulfilled" ? result.value : []);
  if (taskResults.some((result) => result.status === "rejected")) {
    errors.work = "Some project tasks could not be loaded.";
  }

  const projectSummaries = projects.map((project, index) => ({
    project,
    activeTaskCount: taskLists[index].filter((task) => task.status === "active").length,
  }));
  const activeTasks: ActiveTaskRef[] = [];
  const entityById = new Map<string, EntityRef>();
  projects.forEach((project, index) => {
    entityById.set(project.id, {
      kind: "project",
      id: project.id,
      name: project.name,
      projectId: project.id,
      projectName: project.name,
    });
    taskLists[index].forEach((task) => {
      entityById.set(task.id, {
        kind: "task",
        id: task.id,
        name: task.title,
        projectId: project.id,
        projectName: project.name,
      });
      if (task.status === "active") {
        activeTasks.push({ task, projectId: project.id, projectName: project.name });
      }
    });
  });

  if (activityResult.status === "rejected") errors.activity = errorMessage(activityResult.reason);
  if (remindersResult.status === "rejected") errors.reminders = errorMessage(remindersResult.reason);
  if (notificationsResult.status === "rejected") errors.notifications = errorMessage(notificationsResult.reason);
  if (profileResult.status === "rejected") errors.profile = errorMessage(profileResult.reason);

  return {
    projects: projectSummaries,
    activeTasks,
    recentActivity: activityResult.status === "fulfilled" ? activityResult.value.slice(0, 7) : [],
    reminders: remindersResult.status === "fulfilled" ? remindersResult.value : [],
    notifications: notificationsResult.status === "fulfilled" ? notificationsResult.value : [],
    profile: profileResult.status === "fulfilled" ? profileResult.value : null,
    entityById,
    errors,
  };
}
