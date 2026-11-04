# The learner and author UI as static files behind nginx; /api/* goes to the api container.
# The build (`npm run build`, dist/) happens before the image: on the deploy runner, or on the
# developer's machine for a local compose. The host may lack the CPU features the bundler's
# native binaries want, so it never builds the site itself.
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY dist /usr/share/nginx/html
EXPOSE 80
