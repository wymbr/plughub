import React from 'react'
import { Link } from 'react-router-dom'

interface PageHeaderProps {
  title: string
  breadcrumbs?: { label: string; href?: string }[]
  actionButton?: React.ReactNode
  className?: string
}

const PageHeader: React.FC<PageHeaderProps> = ({ title, breadcrumbs, actionButton, className = '' }) => {
  return (
    <div className={`mb-6 ${className}`}>
      {breadcrumbs && breadcrumbs.length > 0 && (
        <nav aria-label="Breadcrumb" className="flex gap-2 text-xs text-muted mb-2">
          {breadcrumbs.map((crumb, idx) => (
            <React.Fragment key={idx}>
              {idx > 0 && <span aria-hidden="true">/</span>}
              {crumb.href ? (
                <Link to={crumb.href} className="text-primary hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary rounded">
                  {crumb.label}
                </Link>
              ) : (
                <span aria-current="page">{crumb.label}</span>
              )}
            </React.Fragment>
          ))}
        </nav>
      )}

      <div className="flex items-center justify-between gap-4">
        <h1 className="text-2xl font-bold text-dark">{title}</h1>
        {actionButton && (
          <div className="flex-shrink-0">
            {actionButton}
          </div>
        )}
      </div>
    </div>
  )
}

export default PageHeader
