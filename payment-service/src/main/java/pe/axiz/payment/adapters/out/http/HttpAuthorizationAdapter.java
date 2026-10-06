package pe.axiz.payment.adapters.out.http;

import java.io.IOException;
import java.net.*;
import java.net.http.*;
import java.time.Duration;
import java.util.Map;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;
import pe.axiz.payment.application.ports.out.AuthorizationPort;
import pe.axiz.payment.configuration.ResiliencePolicies;
import pe.axiz.payment.domain.Payment;
import software.amazon.awssdk.services.secretsmanager.SecretsManagerClient;
import tools.jackson.databind.ObjectMapper;

@Component
public class HttpAuthorizationAdapter implements AuthorizationPort {
  private final HttpClient client =
      HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build();
  private final ObjectMapper json;
  private final ResiliencePolicies resilience;
  private final String base;
  private final String key;
  private final Duration deadline;

  public HttpAuthorizationAdapter(
      ObjectMapper json, ResiliencePolicies resilience, Environment e, SecretsManagerClient sm) {
    this.json = json;
    this.resilience = resilience;
    base = e.getRequiredProperty("axiz.acquirer-url");
    key =
        sm.getSecretValue(b -> b.secretId(e.getRequiredProperty("axiz.internal-secret-id")))
            .secretString();
    deadline = Duration.ofMillis(e.getRequiredProperty("axiz.resilience.timeout-ms", Long.class));
  }

  @Override
  public Decision authorize(Payment p) {
    return resilience.execute(
        () -> {
          String body =
              json.writeValueAsString(
                  Map.of(
                      "paymentId",
                      p.id(),
                      "merchantId",
                      p.merchantId(),
                      "amount",
                      p.money().amount(),
                      "currency",
                      p.money().currency(),
                      "token",
                      p.token()));
          var r =
              HttpRequest.newBuilder(URI.create(base + "/internal/authorizations"))
                  .timeout(deadline)
                  .header("Content-Type", "application/json")
                  .header("X-Internal-Key", key)
                  .POST(HttpRequest.BodyPublishers.ofString(body))
                  .build();
          return send(r);
        });
  }

  @Override
  public Decision query(Payment p) {
    return resilience.execute(
        () ->
            send(
                HttpRequest.newBuilder(URI.create(base + "/internal/authorizations/" + p.id()))
                    .timeout(deadline)
                    .header("X-Internal-Key", key)
                    .GET()
                    .build()));
  }

  private Decision send(HttpRequest request) {
    try {
      var response = client.send(request, HttpResponse.BodyHandlers.ofString());
      if (response.statusCode() == 404) return new Decision(Payment.Status.UNKNOWN, null);
      if (response.statusCode() == 429 || response.statusCode() >= 500)
        throw new TransientAcquirerException("El adquirente no está disponible");
      if (response.statusCode() != 200)
        throw new IllegalStateException("El adquirente rechazó el protocolo");
      var n = json.readTree(response.body());
      return new Decision(
          Payment.Status.valueOf(n.get("status").asString()),
          n.hasNonNull("code") ? n.get("code").asString() : null);
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      throw new TransientAcquirerException(e);
    } catch (IOException e) {
      throw new TransientAcquirerException(e);
    }
  }
}
