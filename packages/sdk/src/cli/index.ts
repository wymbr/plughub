#!/usr/bin/env node
/**
 * cli/index.ts
 * Entry point da CLI plughub-sdk.
 * Spec: PlugHub v24.0 seção 4.6j
 */

import { Command } from "commander"
import { registerCertifyCommand }    from "./certify"
import { registerVerifyCommand }     from "./verify"
import { registerValidateCommand }   from "./validate-adapter"
import { registerRegenerateCommand } from "./regenerate"
import { registerSkillExtractCommand } from "./skill-extract"
import { registerProxyCommand }      from "./proxy"
import { registerImportCommand }     from "./import"

const program = new Command()

program
  .name("plughub-sdk")
  .description("PlugHub Platform — ferramentas de desenvolvimento e certificação")
  .version("1.0.0")

registerCertifyCommand(program)
registerImportCommand(program)
registerVerifyCommand(program)
registerValidateCommand(program)
registerRegenerateCommand(program)
registerSkillExtractCommand(program)
registerProxyCommand(program)

program.parse(process.argv)
