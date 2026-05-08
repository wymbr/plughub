/**
 * channel-meta.ts
 * Shared metadata for all supported channel types.
 * Used by GatewayConfigPanel (credentials CRUD) and any channel-aware component.
 */

export interface ChannelFieldDef {
  key:         string
  label:       string
  placeholder: string
  sensitive:   boolean
}

export interface ChannelSettingDef {
  key:         string
  label:       string
  placeholder: string
  type?:       string
}

export interface ChannelMeta {
  label:         string
  icon:          string
  color:         string
  fields:        ChannelFieldDef[]    // sensitive credentials
  settingFields: ChannelSettingDef[]  // non-sensitive settings
}

export const CHANNEL_META: Record<string, ChannelMeta> = {
  whatsapp: {
    label: 'WhatsApp', icon: '💬', color: '#25d366',
    fields: [
      { key: 'access_token',         label: 'Access Token',          placeholder: 'EAAxxxxx…',        sensitive: true  },
      { key: 'phone_number_id',      label: 'Phone Number ID',       placeholder: '1234567890',       sensitive: false },
      { key: 'waba_id',              label: 'WhatsApp Business ID',  placeholder: '9876543210',       sensitive: false },
      { key: 'webhook_verify_token', label: 'Webhook Verify Token',  placeholder: 'my-verify-token', sensitive: true  },
    ],
    settingFields: [
      { key: 'api_version',  label: 'API Version',  placeholder: 'v19.0' },
      { key: 'webhook_path', label: 'Webhook Path', placeholder: '/webhooks/whatsapp' },
    ],
  },
  webchat: {
    label: 'Webchat', icon: '🌐', color: '#3b82f6',
    fields: [
      { key: 'jwt_secret', label: 'JWT Secret', placeholder: 'changeme-secret-32+chars', sensitive: true },
    ],
    settingFields: [
      { key: 'serving_base_url', label: 'Serving Base URL',         placeholder: 'https://my-domain.com' },
      { key: 'cors_origins',     label: 'CORS Origins (comma-sep)', placeholder: 'https://app.company.com' },
    ],
  },
  voice: {
    label: 'Voice', icon: '📞', color: '#8b5cf6',
    fields: [
      { key: 'api_key',     label: 'API Key',    placeholder: 'sk-…',   sensitive: true  },
      { key: 'api_secret',  label: 'API Secret', placeholder: 'secret', sensitive: true  },
      { key: 'account_sid', label: 'Account SID',placeholder: 'ACxxx',  sensitive: false },
    ],
    settingFields: [
      { key: 'inbound_number', label: 'Inbound Number', placeholder: '+15551234567' },
      { key: 'provider',       label: 'Provider',        placeholder: 'twilio | vonage | sinch' },
      { key: 'region',         label: 'Region',          placeholder: 'us1' },
    ],
  },
  email: {
    label: 'Email', icon: '✉️', color: '#f59e0b',
    fields: [
      { key: 'smtp_password', label: 'SMTP Password', placeholder: '••••••••',  sensitive: true },
      { key: 'api_key',       label: 'API Key',        placeholder: 'SG.xxxxx', sensitive: true },
    ],
    settingFields: [
      { key: 'smtp_host',    label: 'SMTP Host',    placeholder: 'smtp.sendgrid.net' },
      { key: 'smtp_port',    label: 'SMTP Port',    placeholder: '587', type: 'number' },
      { key: 'from_address', label: 'From Address', placeholder: 'support@company.com' },
      { key: 'from_name',    label: 'From Name',    placeholder: 'Support Team' },
      { key: 'provider',     label: 'Provider',     placeholder: 'sendgrid | ses | smtp' },
    ],
  },
  sms: {
    label: 'SMS', icon: '📱', color: '#ec4899',
    fields: [
      { key: 'api_key',    label: 'API Key',    placeholder: 'key-…',  sensitive: true },
      { key: 'api_secret', label: 'API Secret', placeholder: 'secret', sensitive: true },
    ],
    settingFields: [
      { key: 'sender_id', label: 'Sender ID', placeholder: '+15551234567' },
      { key: 'provider',  label: 'Provider',  placeholder: 'twilio | vonage | aws-sns' },
    ],
  },
  instagram: {
    label: 'Instagram', icon: '📸', color: '#e1306c',
    fields: [
      { key: 'access_token',         label: 'Page Access Token',    placeholder: 'EAAxxxxx…',       sensitive: true },
      { key: 'app_secret',           label: 'App Secret',           placeholder: 'app_secret',      sensitive: true },
      { key: 'webhook_verify_token', label: 'Webhook Verify Token', placeholder: 'my-verify-token', sensitive: true },
    ],
    settingFields: [
      { key: 'page_id',     label: 'Instagram Page ID', placeholder: '1234567890' },
      { key: 'api_version', label: 'API Version',        placeholder: 'v19.0' },
    ],
  },
  telegram: {
    label: 'Telegram', icon: '✈️', color: '#2aabee',
    fields: [
      { key: 'bot_token', label: 'Bot Token', placeholder: '1234567890:ABC…', sensitive: true },
    ],
    settingFields: [
      { key: 'webhook_path', label: 'Webhook Path', placeholder: '/webhooks/telegram' },
      { key: 'bot_username', label: 'Bot Username', placeholder: '@mybot' },
    ],
  },
  webrtc: {
    label: 'WebRTC', icon: '🎥', color: '#06b6d4',
    fields: [
      { key: 'turn_secret', label: 'TURN Secret', placeholder: 'secret', sensitive: true },
    ],
    settingFields: [
      { key: 'stun_url',      label: 'STUN URL',      placeholder: 'stun:stun.l.google.com:19302' },
      { key: 'turn_url',      label: 'TURN URL',      placeholder: 'turn:turn.company.com:3478' },
      { key: 'turn_username', label: 'TURN Username', placeholder: 'plughub' },
    ],
  },
  webhook: {
    label: 'Webhook', icon: '🔗', color: '#6366f1',
    fields: [],
    settingFields: [],
  },
}
