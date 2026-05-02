/**
 * WorkflowCalendarPage — /workflow/calendar  (and /workflow/triggers)
 *
 * Webhooks / trigger management for workflows.
 * Calendars were moved to /config/calendars (platform-level config).
 */
import React from 'react'
import WebhooksTab from './WebhooksTab'

export default function WorkflowCalendarPage() {
  return (
    <div style={{ display: 'flex', height: '100%', backgroundColor: '#0a1628' }}>
      <WebhooksTab />
    </div>
  )
}
