/**
 * Shared chrome for the auth screens: the wordmark header, the black rule,
 * the crimson panel, and the form column.
 *
 * Fills the viewport rather than rendering a fixed-width card. The mockup's
 * 1280x660 frame is an artboard size, not a layout constraint — pinning to it
 * left dead margins on anything wider. Widths here are proportional and the
 * display type is fluid; only the reading measures (headline, body copy, form
 * column) stay capped, because those are about legibility.
 *
 * The app's <body> is dark (index.css) and out of scope to change here, so
 * this wrapper establishes the light surface itself — including color-scheme,
 * without which native inputs and checkboxes render with dark browser chrome
 * on top of a light page.
 */

import { ReactNode } from 'react'

const COPY = {
  login: {
    headline: 'Stakeholder Engagement Simulator',
    // Tuned so the fluid middle term lands on the mockup's 68px at 1280px
    // wide, then keeps scaling instead of stopping there.
    headlineClass: 'text-[32px] lg:text-[clamp(40px,5.3vw,76px)] tracking-[-0.03em]',
    body: 'Interview AI stakeholder personas for your Global Projects work. Sign in to start a session.',
  },
  register: {
    headline: 'Join the Lab',
    // Likewise 88px at 1280px wide.
    headlineClass: 'text-[40px] lg:text-[clamp(48px,6.9vw,100px)] tracking-[-0.035em]',
    body: 'Access is open to the WPI community. Request an account with your @wpi.edu address.',
  },
}

export default function AuthLayout({
  variant,
  children,
}: {
  variant: 'login' | 'register'
  children: ReactNode
}) {
  const copy = COPY[variant]

  return (
    <div className="flex min-h-screen flex-col bg-white font-poppins text-ink [color-scheme:light]">
      <header className="flex h-[54px] shrink-0 items-center justify-between px-[18px] lg:h-[68px] lg:px-11">
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
      </header>

      <div className="h-1.5 shrink-0 bg-ink lg:h-2" />

      <div className="flex flex-1 flex-col items-stretch lg:flex-row">
        <section className="relative bg-brand-rose lg:w-[55%]">
          <div className="absolute inset-0 bg-brand [clip-path:polygon(0_0,100%_0,100%_84%,0_100%)] lg:[clip-path:polygon(0_0,100%_0,100%_90%,0_97%)]" />
          <div className="relative flex h-full flex-col justify-center px-[18px] pb-[34px] pt-[26px] lg:px-14 lg:pb-24 lg:pt-[74px]">
            {/* Capped and centred so the copy block stays a readable measure
                on wide screens instead of drifting to the far left. */}
            <div className="mx-auto w-full max-w-[640px]">
              <div className="mb-3 text-[10px] font-semibold uppercase tracking-[0.2em] text-white/80 lg:mb-5 lg:text-[12px]">
                The Global Lab
              </div>
              <h1
                className={`font-extrabold uppercase leading-[0.92] text-white ${copy.headlineClass}`}
              >
                {copy.headline}
              </h1>
              <p className="mt-4 hidden max-w-[440px] text-[16.5px] font-light leading-[1.65] text-white/90 lg:mt-[26px] lg:block">
                {copy.body}
              </p>
            </div>
          </div>
        </section>

        <section className="relative flex flex-1 items-center justify-center bg-white px-[18px] pb-[34px] pt-[26px] lg:w-[45%] lg:flex-none lg:px-14 lg:pb-24 lg:pt-16">
          <div className="relative z-[2] w-full max-w-[420px]">{children}</div>
          <div className="absolute inset-x-0 bottom-0 h-11 bg-brand-rose [clip-path:polygon(0_55%,100%_10%,100%_100%,0_100%)] lg:h-[74px] lg:[clip-path:polygon(0_62%,100%_12%,100%_100%,0_100%)]" />
        </section>
      </div>
    </div>
  )
}
