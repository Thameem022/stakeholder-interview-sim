import React from 'react'

import { useAuth } from '../auth/AuthContext'

/**
 * App chrome in the Global Lab theme: wordmark bar, black rule, then the
 * crimson hero with the rose wedge along its bottom edge — the same
 * construction as the auth screens, so the two surfaces read as one site.
 */
export const Header: React.FC = () => {
  const { user, signOut } = useAuth()

  return (
    <header>
      <div className="flex h-[54px] items-center justify-between px-[18px] lg:h-[68px] lg:px-11">
        <div className="font-slab text-[18px] font-bold tracking-[-0.01em] lg:text-[27px]">
          The Global Lab<span className="pl-1 text-brand lg:pl-1.5">.</span>
        </div>
        <img
          src="/wpi-logo.png"
          alt="Worcester Polytechnic Institute"
          width={447}
          height={164}
          className="h-[22px] w-auto lg:h-[34px]"
        />
      </div>

      <div className="h-1.5 bg-ink lg:h-2" />

      <div className="bg-brand-rose">
        <div className="bg-brand pb-12 pt-7 [clip-path:polygon(0_0,100%_0,100%_84%,0_100%)] lg:pb-16 lg:pt-10">
          <div className="mx-auto flex max-w-[1200px] flex-col gap-6 px-[18px] lg:flex-row lg:items-end lg:justify-between lg:px-11">
            <div className="min-w-0">
              <div className="mb-3 text-[10px] font-semibold uppercase tracking-[0.2em] text-white/80 lg:text-[11px]">
                The Global Lab
              </div>
              <h1 className="max-w-[720px] font-extrabold uppercase leading-[0.95] tracking-[-0.02em] text-white text-[30px] lg:text-[clamp(34px,3.4vw,52px)]">
                Stakeholder Engagement Simulator
              </h1>
              <p className="mt-3 max-w-[460px] text-[14px] font-light leading-[1.6] text-white/90 lg:text-[15px]">
                Voice-powered interviews with the Harbortown stakeholder personas.
              </p>
            </div>

            {user && (
              <div className="flex shrink-0 items-center gap-4 pb-1">
                <span className="text-[14px] text-white/95">
                  {user.first_name} {user.last_name}
                </span>
                <button
                  onClick={signOut}
                  className="h-9 rounded-md bg-white px-4 text-[11px] font-semibold uppercase tracking-[0.1em] text-brand transition-colors hover:bg-white/90"
                >
                  Sign out
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  )
}
