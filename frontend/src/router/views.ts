import type { Component } from 'vue'
import type { RouteName } from './registry'

type Loader = () => Promise<Component>

export const VIEWS: Record<Exclude<RouteName, 'home'>, Loader> = {
  signin: () => import('@/views/SignInView.vue'),
  invite: () => import('@/views/InviteAcceptView.vue'),
  waiting: () => import('@/views/WaitingView.vue'),
  account: () => import('@/views/AccountView.vue'),
  courses: () => import('@/views/CoursesView.vue'),
  'course-sources': () => import('@/views/course/SourcesView.vue'),
  'course-plan': () => import('@/views/course/PlanView.vue'),
  'course-generate': () => import('@/views/course/GenerateView.vue'),
  'course-fix': () => import('@/views/course/FixView.vue'),
  'course-preview': () => import('@/views/course/PreviewView.vue'),
  'course-preview-topic': () => import('@/views/course/PreviewTopicView.vue'),
  'course-preview-sources': () => import('@/views/course/PreviewSourcesView.vue'),
  'course-preview-glossary': () => import('@/views/course/PreviewGlossaryView.vue'),
  'course-publish': () => import('@/views/course/PublishView.vue'),
  'course-runs': () => import('@/views/course/RunsView.vue'),
  'course-chains': () => import('@/views/course/ChainsView.vue'),
  run: () => import('@/views/RunView.vue'),
  learn: () => import('@/views/LearnView.vue'),
  'learn-course': () => import('@/views/LearnCourseView.vue'),
  'learn-topic': () => import('@/views/LearnTopicView.vue'),
  'learn-course-sources': () => import('@/views/LearnSourcesView.vue'),
  'learn-course-glossary': () => import('@/views/LearnGlossaryView.vue'),
  'course-report': () => import('@/views/course/ReportView.vue'),
  users: () => import('@/views/UsersView.vue'),
  invites: () => import('@/views/InvitesView.vue'),
  roles: () => import('@/views/RolesView.vue'),
  'not-found': () => import('@/views/NotFoundView.vue'),
}
