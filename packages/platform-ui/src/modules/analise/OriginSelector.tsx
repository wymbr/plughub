/**
 * OriginSelector — seletor de procedência do substrato de avaliação (ADR
 * adr-quality-substrate-isolation). Default "Produção" (live); incluir
 * Importação/Reavaliação é ação explícita. Compartilhado pelas telas de Analytics
 * (Sessions, Pools, Agents). O multiselect de pool vive DENTRO da origem escolhida
 * (a origem é o eixo; um mesmo pool pode ser origin-misto no reuso do R13d).
 */
import React from 'react'
import { useTranslation } from 'react-i18next'

export type Origin = 'live' | 'import' | 'reeval'

export const ORIGIN_VALUES: Origin[] = ['live', 'import', 'reeval']

interface Props {
  value: Origin
  onChange: (o: Origin) => void
  className?: string
}

const OriginSelector: React.FC<Props> = ({ value, onChange, className }) => {
  const { t } = useTranslation('agentReports')
  return (
    <label className={`flex items-center gap-1.5 ${className ?? ''}`} title={t('origin.hint')}>
      <span className="text-xs text-muted">{t('origin.label')}</span>
      <select
        value={value}
        onChange={e => onChange(e.target.value as Origin)}
        className="text-sm border border-border rounded px-2 py-1 bg-white"
      >
        {ORIGIN_VALUES.map(o => (
          <option key={o} value={o}>{t(`origin.${o}`)}</option>
        ))}
      </select>
    </label>
  )
}

export default OriginSelector
