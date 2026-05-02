/**
 * docker-client.ts
 * Docker CLI helpers for container lifecycle management in E2E tests.
 * Used by Scenario 3 (resume after failure).
 */

import { exec } from "child_process";
import { promisify } from "util";

const execAsync = promisify(exec);

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Kills a running container by name (SIGKILL).
 */
export async function killContainer(containerName: string): Promise<void> {
  await execAsync(`docker kill ${containerName}`);
  console.log(`[docker] Killed container: ${containerName}`);
}

/**
 * Starts a stopped container by name.
 */
export async function startContainer(containerName: string): Promise<void> {
  await execAsync(`docker start ${containerName}`);
  console.log(`[docker] Started container: ${containerName}`);
}

/**
 * Polls `docker inspect` until the container's health status is "healthy",
 * or throws after timeoutMs.
 */
export async function waitForContainerHealth(
  containerName: string,
  timeoutMs: number = 30000
): Promise<void> {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    try {
      const { stdout } = await execAsync(
        `docker inspect --format="{{.State.Health.Status}}" ${containerName}`
      );
      const status = stdout.trim().replace(/"/g, "");
      if (status === "healthy") {
        console.log(`[docker] Container ${containerName} is healthy`);
        return;
      }
    } catch {
      // container might not be running yet
    }
    await sleep(1000);
  }

  throw new Error(
    `[docker] Timeout waiting for container ${containerName} to become healthy after ${timeoutMs}ms`
  );
}

/**
 * Returns the current status of a container (running, exited, etc.).
 */
export async function getContainerStatus(
  containerName: string
): Promise<string> {
  const { stdout } = await execAsync(
    `docker inspect --format="{{.State.Status}}" ${containerName}`
  );
  return stdout.trim().replace(/"/g, "");
}
