/**
 * Presentation metadata for the persona cards.
 *
 * The API deliberately returns only {key, display_name}; the role tags and
 * blurbs are copy, and the images reuse the interview backdrops that already
 * ship in public/background (same convention Avatar.tsx uses). Unknown keys
 * fall back to an empty meta so a newly added persona still renders a card.
 */

export interface PersonaMeta {
  role: string
  description: string
  image?: string
}

const META: Record<string, PersonaMeta> = {
  alex_martinez: {
    role: 'Municipal planner',
    description:
      'Roleplays a municipal planner working the Harbortown floodplain question from inside the process.',
    image: '/background/alex_martinez_bg.jpg',
  },
  michael_mike_alvarez: {
    role: 'Waterfront resident',
    description:
      'Three decades on the working waterfront. Holds no formal role, but remembers every adjustment the town has made.',
    image: '/background/michael_mike_alvarez_bg.avif',
  },
  sarah_donnelly: {
    role: 'Small business owner',
    description:
      'Runs a downtown business in the most exposed, most visible part of town. Judges plans on credibility and feasibility.',
    image: '/background/sarah_donnelly_bg.jpg',
  },
  thomas_tom_caldwell: {
    role: 'Developer',
    description:
      'Indirect but significant power: capital, timing, and the ability to move projects when others cannot.',
    image: '/background/thomas_tom_caldwell_bg.jpg',
  },
}

export const personaMeta = (key: string): PersonaMeta =>
  META[key] ?? { role: '', description: '' }
