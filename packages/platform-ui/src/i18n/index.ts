import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import commonPtBr from './locales/pt-BR/common.json'
import shellPtBr from './locales/pt-BR/shell.json'
import configRecursosPtBr from './locales/pt-BR/configRecursos.json'
import commonEn from './locales/en/common.json'
import shellEn from './locales/en/shell.json'
import configRecursosEn from './locales/en/configRecursos.json'

i18n.use(initReactI18next).init({
  resources: {
    'pt-BR': {
      common: commonPtBr,
      shell: shellPtBr,
      configRecursos: configRecursosPtBr
    },
    en: {
      common: commonEn,
      shell: shellEn,
      configRecursos: configRecursosEn
    }
  },
  lng: 'pt-BR',
  fallbackLng: 'pt-BR',
  ns: ['common', 'shell', 'configRecursos'],
  defaultNS: 'common',
  interpolation: {
    escapeValue: false
  }
})

export default i18n
