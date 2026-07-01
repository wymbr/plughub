import React, { useState } from 'react'
import { useTranslation } from 'react-i18next'
import PageHeader from '@/components/ui/PageHeader'
import PoolsPage from './PoolsPage'
import SkillsPage from './SkillsPage'
import LlmAccountsPage from './LlmAccountsPage'

type Tab = 'pools' | 'skills' | 'llmAccounts'

const ConfigRecursosIndex: React.FC = () => {
  const { t } = useTranslation('configRecursos')
  const [activeTab, setActiveTab] = useState<Tab>('pools')

  const tabs: { id: Tab; label: string }[] = [
    { id: 'pools',       label: t('tabs.pools')  },
    { id: 'skills',      label: t('tabs.skills') },
    { id: 'llmAccounts', label: t('tabs.llmAccounts') },
  ]

  return (
    <div>
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
        {activeTab === 'skills'      && <SkillsPage />}
        {activeTab === 'llmAccounts' && <LlmAccountsPage />}
      </div>
    </div>
  )
}

export default ConfigRecursosIndex
