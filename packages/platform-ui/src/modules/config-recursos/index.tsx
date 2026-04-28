import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import PageHeader from '@/components/ui/PageHeader'
import PoolsPage from './PoolsPage'
import AgentTypesPage from './AgentTypesPage'
import SkillsPage from './SkillsPage'
import InstancesPage from './InstancesPage'
import ChannelsPage from './ChannelsPage'
import HumanAgentsPage from './HumanAgentsPage'

type Tab = 'pools' | 'agentTypes' | 'skills' | 'instances' | 'channels' | 'humanAgents'

const ConfigRecursosIndex: React.FC = () => {
  const { t } = useTranslation('configRecursos')
  const [activeTab, setActiveTab] = useState<Tab>('pools')

  const tabs: { id: Tab; label: string }[] = [
    { id: 'pools',       label: t('tabs.pools') },
    { id: 'agentTypes',  label: t('tabs.agentTypes') },
    { id: 'skills',      label: t('tabs.skills') },
    { id: 'instances',   label: t('tabs.instances') },
    { id: 'channels',    label: t('tabs.channels') },
    { id: 'humanAgents', label: t('tabs.humanAgents') },
  ]

  return (
    <div>
      <PageHeader title={t('title')} />

      <div className="mb-6 border-b border-lightGray flex gap-8">
        {tabs.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`py-3 px-1 font-semibold transition-colors border-b-2 ${
              activeTab === tab.id
                ? 'text-primary border-primary'
                : 'text-gray border-transparent hover:text-dark'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="mt-6">
        {activeTab === 'pools'       && <PoolsPage />}
        {activeTab === 'agentTypes'  && <AgentTypesPage />}
        {activeTab === 'skills'      && <SkillsPage />}
        {activeTab === 'instances'   && <InstancesPage />}
        {activeTab === 'channels'    && <ChannelsPage />}
        {activeTab === 'humanAgents' && <HumanAgentsPage />}
      </div>
    </div>
  )
}

export default ConfigRecursosIndex
