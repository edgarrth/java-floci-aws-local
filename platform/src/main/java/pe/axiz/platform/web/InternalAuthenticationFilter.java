package pe.axiz.platform.web;

import jakarta.servlet.*;
import jakarta.servlet.http.*;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import software.amazon.awssdk.services.secretsmanager.SecretsManagerClient;

@Component
public class InternalAuthenticationFilter extends OncePerRequestFilter {
  private final byte[] secret;

  public InternalAuthenticationFilter(Environment env, SecretsManagerClient sm) {
    secret =
        sm.getSecretValue(b -> b.secretId(env.getRequiredProperty("axiz.internal-secret-id")))
            .secretString()
            .getBytes(StandardCharsets.UTF_8);
  }

  @Override
  protected boolean shouldNotFilter(HttpServletRequest r) {
    return !r.getRequestURI().startsWith("/internal/");
  }

  @Override
  protected void doFilterInternal(HttpServletRequest r, HttpServletResponse s, FilterChain chain)
      throws ServletException, IOException {
    String token = r.getHeader("X-Internal-Key");
    if (token == null || !MessageDigest.isEqual(secret, token.getBytes(StandardCharsets.UTF_8))) {
      s.sendError(401, "Se requiere autenticación interna");
      return;
    }
    chain.doFilter(r, s);
  }
}
