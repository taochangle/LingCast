package router

import (
	"net/http"
	"regexp"
	"strings"
	"time"

	"github.com/gin-contrib/cors"
	"github.com/gin-gonic/gin"
	swaggerFiles "github.com/swaggo/files"
	ginSwagger "github.com/swaggo/gin-swagger"

	"talkingavatar/backend/internal/config"
	"talkingavatar/backend/internal/i18n"
)

// Base returns a Gin engine with the shared middleware stack (logging,
// recovery, language middleware and CORS). Each microservice registers its
// own routes on top of this engine.
func Base(cfg config.Config) *gin.Engine {
	gin.SetMode(cfg.GinMode)

	r := gin.New()
	r.Use(gin.Logger(), gin.Recovery())
	r.Use(i18n.Middleware())

	// CORS must allow the Vite dev server (e.g. http://localhost:5173) as
	// well as the nginx-served app origin.
	r.Use(cors.New(cors.Config{
		AllowOrigins:           cfg.CORSOrigins,
		AllowBrowserExtensions: true,
		AllowOriginWithContextFunc: func(c *gin.Context, origin string) bool {
			return strings.HasPrefix(c.Request.URL.Path, "/api/collector/") && regexp.MustCompile(`^chrome-extension://[a-p]{32}$`).MatchString(origin)
		},
		AllowMethods:     []string{"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"},
		AllowHeaders:     []string{"Origin", "Content-Type", "Accept", "Authorization", "Accept-Language"},
		AllowCredentials: true,
		MaxAge:           12 * time.Hour,
	}))

	r.GET("/healthz", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "ok"})
	})
	// Swagger UI + OpenAPI JSON at /swagger/index.html (docs are generated
	// by `swag init` into backend/docs; each service main imports it).
	r.GET("/swagger/*any", ginSwagger.WrapHandler(swaggerFiles.Handler))
	return r
}
