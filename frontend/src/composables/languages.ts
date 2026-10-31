export const COURSE_LANGUAGES = ['en', 'nb', 'uk'] as const
export type CourseLanguage = (typeof COURSE_LANGUAGES)[number]
