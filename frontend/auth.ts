/**
 * next-auth v5 (Auth.js) config.
 *
 * Google OAuth2 provider — frontend session + ID token retrieval.
 * The ID token is passed to FastAPI as Authorization: Bearer <id_token>.
 * FastAPI validates it against Google JWKS and extracts google_sub.
 *
 * Required env vars:
 *   GOOGLE_CLIENT_ID     — from Google Cloud Console
 *   GOOGLE_CLIENT_SECRET — from Google Cloud Console
 *   NEXTAUTH_SECRET      — openssl rand -hex 32
 */

import NextAuth from "next-auth";
import Google from "next-auth/providers/google";
import type { Session } from "next-auth";
import type { JWT } from "next-auth/jwt";

export const { handlers, auth, signIn, signOut } = NextAuth({
  providers: [
    Google({
      clientId: process.env.GOOGLE_CLIENT_ID!,
      clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
      authorization: {
        params: {
          // Request offline access so Google returns an id_token
          access_type: "offline",
          prompt: "consent",
        },
      },
    }),
  ],

  callbacks: {
    async jwt({ token, account }): Promise<JWT> {
      // On first sign-in, account contains the Google id_token
      if (account?.id_token) {
        token.id_token = account.id_token;
      }
      return token;
    },

    async session({ session, token }): Promise<Session> {
      // Expose id_token on the session so API calls can use it
      if (token.id_token) {
        (session as Session & { id_token: string }).id_token = token.id_token as string;
      }
      return session;
    },
  },

  pages: {
    signIn: "/auth/signin",
    error: "/auth/error",
  },
});

// Extend next-auth types to include id_token
declare module "next-auth" {
  interface Session {
    id_token?: string;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    id_token?: string;
  }
}
