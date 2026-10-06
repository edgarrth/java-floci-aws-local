package pe.axiz.payment.adapters.out.aws;

import java.math.BigDecimal;
import java.time.Duration;
import org.springframework.context.annotation.*;
import org.springframework.core.env.Environment;
import org.springframework.data.redis.connection.lettuce.LettuceConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;
import pe.axiz.payment.application.ports.out.MerchantPolicyPort;
import software.amazon.awssdk.services.ssm.SsmClient;
import software.amazon.awssdk.services.ssm.model.ParameterNotFoundException;

@Component
public class MerchantPolicyAdapter implements MerchantPolicyPort {
  private final SsmClient ssm;
  private final StringRedisTemplate redis;

  public MerchantPolicyAdapter(SsmClient ssm, StringRedisTemplate redis) {
    this.ssm = ssm;
    this.redis = redis;
  }

  public BigDecimal limit(String merchant) {
    String key = "merchant:" + merchant + ":limit";
    String cached = null;
    try {
      cached = redis.opsForValue().get(key);
    } catch (RuntimeException ignored) {
      /* Se consulta la fuente autoritativa cuando la caché no está disponible. */
    }
    if (cached != null) return new BigDecimal(cached);
    String value;
    try {
      value =
          ssm.getParameter(b -> b.name("/axiz/merchants/" + merchant + "/limit"))
              .parameter()
              .value();
    } catch (ParameterNotFoundException exception) {
      throw new IllegalArgumentException(
          "El comercio no tiene una política configurada", exception);
    }
    try {
      redis.opsForValue().set(key, value, Duration.ofSeconds(30));
    } catch (RuntimeException ignored) {
      /* La escritura de caché no modifica la decisión comercial. */
    }
    return new BigDecimal(value);
  }

  @Configuration
  static class RedisConfiguration {
    @Bean
    LettuceConnectionFactory redisConnectionFactory(Environment e) {
      var standalone =
          new org.springframework.data.redis.connection.RedisStandaloneConfiguration(
              e.getRequiredProperty("axiz.redis-host"),
              e.getRequiredProperty("axiz.redis-port", Integer.class));
      var config =
          org.springframework.data.redis.connection.lettuce.LettuceClientConfiguration.builder()
              .commandTimeout(Duration.ofMillis(500))
              .shutdownTimeout(Duration.ZERO)
              .build();
      return new LettuceConnectionFactory(standalone, config);
    }
  }
}
