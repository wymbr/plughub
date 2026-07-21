import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

// pt-BR
import commonPtBr from './locales/pt-BR/common.json'
import shellPtBr from './locales/pt-BR/shell.json'
import configRecursosPtBr from './locales/pt-BR/configRecursos.json'
import contactsPtBr from './locales/pt-BR/contacts.json'
import billingPtBr from './locales/pt-BR/billing.json'
import evaluationPtBr from './locales/pt-BR/evaluation.json'
import accessPtBr from './locales/pt-BR/access.json'
import maskingPtBr from './locales/pt-BR/masking.json'
import agentAssistPtBr from './locales/pt-BR/agentAssist.json'
import agentReportsPtBr from './locales/pt-BR/agentReports.json'
import workflowsPtBr from './locales/pt-BR/workflows.json'
import agentFlowPtBr from './locales/pt-BR/agentFlow.json'
import calendarsPtBr from './locales/pt-BR/calendars.json'
import servicePtBr from './locales/pt-BR/atendimento.json'
import campaignsPtBr from './locales/pt-BR/campaigns.json'
import configPlataformaPtBr from './locales/pt-BR/configPlataforma.json'
import dashboardsPtBr from './locales/pt-BR/dashboards.json'
import homePtBr from './locales/pt-BR/home.json'
import groupsPtBr from './locales/pt-BR/groups.json'
import auditPtBr from './locales/pt-BR/audit.json'
import channelsPtBr from './locales/pt-BR/channels.json'
import webrtcPtBr from './locales/pt-BR/webrtc.json'
import dialogFormsPtBr from './locales/pt-BR/dialogForms.json'
import schedulerPtBr from './locales/pt-BR/scheduler.json'

// en
import commonEn from './locales/en/common.json'
import shellEn from './locales/en/shell.json'
import configRecursosEn from './locales/en/configRecursos.json'
import contactsEn from './locales/en/contacts.json'
import billingEn from './locales/en/billing.json'
import evaluationEn from './locales/en/evaluation.json'
import accessEn from './locales/en/access.json'
import maskingEn from './locales/en/masking.json'
import agentAssistEn from './locales/en/agentAssist.json'
import agentReportsEn from './locales/en/agentReports.json'
import workflowsEn from './locales/en/workflows.json'
import agentFlowEn from './locales/en/agentFlow.json'
import calendarsEn from './locales/en/calendars.json'
import serviceEn from './locales/en/atendimento.json'
import campaignsEn from './locales/en/campaigns.json'
import configPlataformaEn from './locales/en/configPlataforma.json'
import dashboardsEn from './locales/en/dashboards.json'
import homeEn from './locales/en/home.json'
import groupsEn from './locales/en/groups.json'
import auditEn from './locales/en/audit.json'
import channelsEn from './locales/en/channels.json'
import webrtcEn from './locales/en/webrtc.json'
import dialogFormsEn from './locales/en/dialogForms.json'
import schedulerEn from './locales/en/scheduler.json'

i18n.use(initReactI18next).init({
  resources: {
    'pt-BR': {
      common:          commonPtBr,
      shell:           shellPtBr,
      configRecursos:  configRecursosPtBr,
      contacts:        contactsPtBr,
      billing:         billingPtBr,
      evaluation:      evaluationPtBr,
      access:          accessPtBr,
      masking:         maskingPtBr,
      agentAssist:     agentAssistPtBr,
      agentReports:    agentReportsPtBr,
      workflows:       workflowsPtBr,
      agentFlow:       agentFlowPtBr,
      calendars:       calendarsPtBr,
      service:         servicePtBr,
      campaigns:       campaignsPtBr,
      configPlataforma:configPlataformaPtBr,
      dashboards:      dashboardsPtBr,
      home:            homePtBr,
      groups:          groupsPtBr,
      audit:           auditPtBr,
      channels:        channelsPtBr,
      webrtc:          webrtcPtBr,
      dialogForms:     dialogFormsPtBr,
      scheduler:       schedulerPtBr,
    },
    en: {
      common:          commonEn,
      shell:           shellEn,
      configRecursos:  configRecursosEn,
      contacts:        contactsEn,
      billing:         billingEn,
      evaluation:      evaluationEn,
      access:          accessEn,
      masking:         maskingEn,
      agentAssist:     agentAssistEn,
      agentReports:    agentReportsEn,
      workflows:       workflowsEn,
      agentFlow:       agentFlowEn,
      calendars:       calendarsEn,
      service:         serviceEn,
      campaigns:       campaignsEn,
      configPlataforma:configPlataformaEn,
      dashboards:      dashboardsEn,
      home:            homeEn,
      groups:          groupsEn,
      audit:           auditEn,
      channels:        channelsEn,
      webrtc:          webrtcEn,
      dialogForms:     dialogFormsEn,
      scheduler:       schedulerEn,
    }
  },
  lng: 'en',
  fallbackLng: 'en',
  ns: [
    'common', 'shell', 'configRecursos',
    'contacts', 'billing', 'evaluation', 'access', 'masking',
    'agentAssist', 'agentReports', 'workflows', 'agentFlow',
    'calendars', 'service', 'campaigns', 'configPlataforma',
    'dashboards', 'home', 'groups', 'audit', 'channels', 'webrtc', 'dialogForms',
    'scheduler',
  ],
  defaultNS: 'common',
  interpolation: {
    escapeValue: false
  }
})

export default i18n
