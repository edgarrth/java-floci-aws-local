package pe.axiz.platform.web;

import jakarta.servlet.*;
import jakarta.servlet.http.*;
import java.io.IOException;
import java.util.UUID;
import org.slf4j.MDC;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
public class CorrelationFilter extends OncePerRequestFilter {
  @Override
  protected void doFilterInternal(
      HttpServletRequest request, HttpServletResponse response, FilterChain chain)
      throws ServletException, IOException {
    String id = request.getHeader("X-Correlation-Id");
    if (id == null || !id.matches("[A-Za-z0-9-]{1,64}")) id = UUID.randomUUID().toString();
    response.setHeader("X-Correlation-Id", id);
    MDC.put("correlationId", id);
    try {
      chain.doFilter(request, response);
    } finally {
      MDC.remove("correlationId");
    }
  }
}
