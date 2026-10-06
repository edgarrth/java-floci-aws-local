package pe.axiz.platform.web;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import software.amazon.awssdk.services.secretsmanager.SecretsManagerClient;

@Component
public class ApiKeyFilter extends OncePerRequestFilter {
  private final byte[] key;

  public ApiKeyFilter(SecretsManagerClient secrets) {
    key =
        secrets
            .getSecretValue(b -> b.secretId("/axiz/api-key"))
            .secretString()
            .getBytes(StandardCharsets.UTF_8);
  }

  @Override
  protected boolean shouldNotFilter(HttpServletRequest request) {
    return !request.getRequestURI().startsWith("/api/");
  }

  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain chain)
      throws IOException, ServletException {
    String value = request.getHeader("X-Api-Key");
    if (value == null || !MessageDigest.isEqual(key, value.getBytes(StandardCharsets.UTF_8))) {
      response.sendError(401, "Se requiere una clave de API válida");
      return;
    }
    chain.doFilter(request, response);
  }
}
