/**
 * report.ts
 * JSON report builder for the E2E test suite.
 */

import { writeFile } from "fs/promises";

export interface Assertion {
  name: string;
  passed: boolean;
  details?: unknown;
  latency_ms?: number;
}

export interface ScenarioResult {
  scenario_id: string;
  name: string;
  passed: boolean;
  assertions: Assertion[];
  duration_ms: number;
  error?: string;
}

export interface Report {
  passed: boolean;
  total: number;
  passed_count: number;
  failed_count: number;
  scenarios: ScenarioResult[];
  generated_at: string;
}

export class ReportBuilder {
  private readonly scenarios: ScenarioResult[] = [];

  addScenario(result: ScenarioResult): void {
    this.scenarios.push(result);
  }

  build(): Report {
    const passed_count = this.scenarios.filter((s) => s.passed).length;
    const failed_count = this.scenarios.length - passed_count;
    return {
      passed: failed_count === 0,
      total: this.scenarios.length,
      passed_count,
      failed_count,
      scenarios: this.scenarios,
      generated_at: new Date().toISOString(),
    };
  }

  async writeToFile(filePath: string): Promise<void> {
    const report = this.build();
    await writeFile(filePath, JSON.stringify(report, null, 2), "utf-8");
  }

  printSummary(): void {
    const report = this.build();

    console.log("\n" + "═".repeat(60));
    console.log("  PlugHub E2E Test Report");
    console.log("═".repeat(60));
    console.log(`  Total:   ${report.total}`);
    console.log(`  Passed:  ${report.passed_count}`);
    console.log(`  Failed:  ${report.failed_count}`);
    console.log("─".repeat(60));

    for (const scenario of report.scenarios) {
      const icon = scenario.passed ? "✅" : "❌";
      console.log(`\n  ${icon} [${scenario.scenario_id}] ${scenario.name}`);
      console.log(`     Duration: ${scenario.duration_ms}ms`);

      if (scenario.error) {
        console.log(`     Error: ${scenario.error}`);
      }

      for (const assertion of scenario.assertions) {
        const aIcon = assertion.passed ? "  ✓" : "  ✗";
        const latency =
          assertion.latency_ms !== undefined
            ? ` (${assertion.latency_ms}ms)`
            : "";
        console.log(`     ${aIcon} ${assertion.name}${latency}`);
        if (!assertion.passed && assertion.details !== undefined) {
          console.log(`       Details: ${JSON.stringify(assertion.details)}`);
        }
      }
    }

    console.log("\n" + "═".repeat(60));
    console.log(
      `  Result: ${report.passed ? "✅ ALL PASSED" : "❌ SOME FAILED"}`
    );
    console.log("═".repeat(60) + "\n");
  }
}

/** Creates a passed assertion. */
export function pass(name: string, details?: unknown, latency_ms?: number): Assertion {
  return { name, passed: true, details, latency_ms };
}

/** Creates a failed assertion. */
export function fail(name: string, details?: unknown, latency_ms?: number): Assertion {
  return { name, passed: false, details, latency_ms };
}

/** Assert helper — throws if condition is false. */
export function assert(condition: boolean, name: string, details?: unknown): Assertion {
  return condition ? pass(name, details) : fail(name, details);
}
