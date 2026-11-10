/**
 * Content for the About page. Edit here - the page renders whatever is present:
 * add `photoUrl` for a profile picture, entries to `links` for social profiles, or more `sections`.
 */
export interface AboutLink { label: string; url: string; kind?: "linkedin" | "github" | "email" | "web" }
export interface AboutSection { title: string; items: { label: string; value: string }[] }

export const ABOUT = {
  name: "Jatin Jain",
  headline: "AI Solutions Analyst candidate · built this PS-3 submission for Zamp",
  photoUrl: null as string | null,
  institution: "Indian Institute of Technology Hyderabad (IIT Hyderabad)",
  sections: [
    {
      title: "Education",
      items: [
        { label: "Institution", value: "Indian Institute of Technology Hyderabad (IIT Hyderabad)" },
        { label: "Degree", value: "B.Tech in Mechanical & Aerospace Engineering" },
        { label: "Additional academics", value: "Double Major in Entrepreneurship · Minor in Economics" },
      ],
    },
  ] as AboutSection[],
  links: [] as AboutLink[],
};

export const initials = (name: string) =>
  name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join("");
