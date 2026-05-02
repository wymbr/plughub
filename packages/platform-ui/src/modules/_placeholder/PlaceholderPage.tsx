import React from 'react'
import { useTranslation } from 'react-i18next'
import EmptyState from '@/components/ui/EmptyState'
import Button from '@/components/ui/Button'

interface PlaceholderPageProps {
  module?: string
  phase?: string
}

const PlaceholderPage: React.FC<PlaceholderPageProps> = ({ module = 'Este', phase = 'Arc 3' }) => {
  const { t } = useTranslation('shell')

  return (
    <div className="max-w-2xl mx-auto">
      <EmptyState
        title={t('placeholder.title')}
        description={`${module} — ${phase}`}
        action={
          <Button variant="secondary">
            Back to Home
          </Button>
        }
      />
    </div>
  )
}

export default PlaceholderPage
