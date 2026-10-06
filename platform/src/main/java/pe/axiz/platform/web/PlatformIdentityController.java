package pe.axiz.platform.web;

import java.net.URI;
import java.time.Duration;
import java.util.Map;
import org.springframework.core.env.Environment;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;
import software.amazon.awssdk.auth.credentials.AwsCredentialsProvider;
import software.amazon.awssdk.http.urlconnection.UrlConnectionHttpClient;
import software.amazon.awssdk.regions.Region;
import software.amazon.awssdk.services.sts.StsClient;

@RestController
public class PlatformIdentityController implements AutoCloseable {
  private final StsClient sts;
  private final JdbcTemplate jdbc;

  public PlatformIdentityController(
      AwsCredentialsProvider credentials, Environment environment, JdbcTemplate jdbc) {
    this.jdbc = jdbc;
    var builder =
        StsClient.builder()
            .credentialsProvider(credentials)
            .region(Region.of(environment.getRequiredProperty("axiz.aws.region")))
            .httpClientBuilder(
                UrlConnectionHttpClient.builder()
                    .connectionTimeout(Duration.ofSeconds(2))
                    .socketTimeout(Duration.ofSeconds(5)));
    String endpoint = environment.getProperty("axiz.aws.endpoint", "");
    if (!endpoint.isBlank()) builder.endpointOverride(URI.create(endpoint));
    sts = builder.build();
  }

  @GetMapping("/internal/platform")
  Object identity() {
    var identity = sts.getCallerIdentity();
    var database =
        jdbc.queryForMap(
            "SELECT current_user AS username,current_schema() AS schema,"
                + "(SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()) AS tls");
    return Map.of("awsRole", identity.arn(), "account", identity.account(), "database", database);
  }

  @Override
  public void close() {
    sts.close();
  }
}
