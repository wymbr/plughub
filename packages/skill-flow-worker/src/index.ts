/**
 * index.ts
 * Entry point for the Skill Flow Worker.
 */

import { loadSettings } from './config'
import { SkillFlowWorker } from './worker'

async function main(): Promise<void> {
  const settings = loadSettings()

  console.log('PlugHub Skill Flow Worker')
  console.log(`Kafka brokers: ${settings.kafkaBrokers.join(', ')}`)
  console.log(`Kafka topic: ${settings.kafkaTopic}`)
  console.log(`Kafka group ID: ${settings.kafkaGroupId}`)
  console.log(`Workflow API: ${settings.workflowApiUrl}`)
  console.log(`Redis: ${settings.redisUrl}`)

  const worker = new SkillFlowWorker(settings)
  await worker.start()
}

main().catch(err => {
  console.error('Fatal error:', err)
  process.exit(1)
})
