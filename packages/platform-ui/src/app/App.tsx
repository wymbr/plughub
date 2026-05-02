import React from 'react'
import { RouterProvider, createBrowserRouter } from 'react-router-dom'
import { AuthProvider } from '@/auth/AuthContext'
import { routes } from './routes'
import '@/i18n'

const router = createBrowserRouter(routes)

const App: React.FC = () => {
  return (
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  )
}

export default App
